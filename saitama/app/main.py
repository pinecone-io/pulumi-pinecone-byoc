"""
Saitama — production FastAPI + Pinecone gRPC query service.

Architecture:
  - Single PineconeGRPC client + Index connection per worker, created at startup
  - Routes are async def; blocking gRPC calls are offloaded via asyncio.to_thread()
    to an explicit ThreadPoolExecutor sized to THREAD_POOL_SIZE
  - gRPC channel keepalive prevents AWS load balancers from silently closing idle connections
  - Per-call timing emitted as CloudWatch EMF metrics (structured JSON to stdout)
"""

import asyncio
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Optional

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pinecone.grpc import PineconeGRPC
from pinecone.grpc.config import GRPCClientConfig
from pydantic import BaseModel, field_validator

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)s}',
)
logger = logging.getLogger("saitama")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
INDEX_HOST       = os.environ.get("INDEX_HOST", "")
VECTOR_DIM       = int(os.environ.get("VECTOR_DIM", "0"))       # 0 = no validation
THREAD_POOL_SIZE = int(os.environ.get("THREAD_POOL_SIZE", "100"))

_MAX_UPSERT_BYTES   = 4_000_000
_MAX_UPSERT_VECTORS = 100


# ---------------------------------------------------------------------------
# Shared state (per worker process)
# ---------------------------------------------------------------------------
class _State:
    pc: Optional[PineconeGRPC] = None
    index: Optional[Any] = None
    executor: Optional[ThreadPoolExecutor] = None

state = _State()


# ---------------------------------------------------------------------------
# EMF metric emission
# Writes CloudWatch Embedded Metric Format JSON to stdout.
# CloudWatch Logs Insights can query these as structured fields.
# When the CW agent is present, they are also extracted as real CW metrics.
# ---------------------------------------------------------------------------
def _emit(metrics: dict[str, float], dimensions: dict[str, str] | None = None) -> None:
    dims = {"Service": "saitama", **(dimensions or {})}
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "Saitama",
                "Dimensions": [list(dims.keys())],
                "Metrics": [
                    {"Name": k, "Unit": "Milliseconds", "StorageResolution": 60}
                    for k in metrics
                ],
            }],
        },
        **dims,
        **metrics,
    }
    print(json.dumps(record), flush=True)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not PINECONE_API_KEY:
        raise RuntimeError("PINECONE_API_KEY is required")
    if not INDEX_HOST:
        raise RuntimeError("INDEX_HOST is required")

    # Explicit thread pool — async def + asyncio.to_thread() uses this executor.
    # Without this, Python defaults to min(32, cpu+4) threads which is far too low.
    state.executor = ThreadPoolExecutor(
        max_workers=THREAD_POOL_SIZE,
        thread_name_prefix="pinecone",
    )
    loop = asyncio.get_event_loop()
    loop.set_default_executor(state.executor)

    # Also align AnyIO's limiter (used by any remaining sync def routes / dependencies)
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = THREAD_POOL_SIZE

    # gRPC channel config:
    #   - keepalive_time_ms: ping every 20s even when idle — prevents AWS NLB/ALB
    #     from silently closing connections (NLB idle timeout = 350s by default)
    #   - keepalive_permit_without_calls: send pings even with no active RPCs
    #   - pool_threads: gRPC C-core internal thread pool, match to our executor size
    grpc_config = GRPCClientConfig(
        reuse_channel=True,
        timeout=10,
        pool_threads=THREAD_POOL_SIZE,
        grpc_channel_options={
            "grpc.keepalive_time_ms":              "20000",
            "grpc.keepalive_timeout_ms":           "10000",
            "grpc.keepalive_permit_without_calls": "1",
            "grpc.max_concurrent_streams":         "0",      # defer to server limit
            "grpc.default_max_recv_message_length": "16777216",  # 16 MB
            "grpc.default_max_send_message_length": "16777216",  # 16 MB
        },
    )

    state.pc    = PineconeGRPC(api_key=PINECONE_API_KEY)
    state.index = state.pc.Index(host=INDEX_HOST, grpc_config=grpc_config)

    logger.info('"Pinecone gRPC connected host=%s threads=%d"', INDEX_HOST, THREAD_POOL_SIZE)

    yield

    logger.info('"Shutting down — draining thread pool"')
    state.index    = None
    state.pc       = None
    state.executor.shutdown(wait=True)
    state.executor = None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Saitama",
    description="Production Pinecone gRPC query service",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware — total request latency logged + emitted as EMF
# ---------------------------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    total_ms = (time.perf_counter() - start) * 1000
    logger.info(
        '"method":"%s","path":"%s","status":%d,"total_ms":%.2f',
        request.method, request.url.path, response.status_code, total_ms,
    )
    if request.url.path not in ("/healthz", "/stats"):
        _emit({"TotalRequestLatencyMs": total_ms})
    return response


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    vector: list[float]
    top_k: int = 10
    namespace: Optional[str] = None
    include_values: bool = False
    include_metadata: bool = True
    filter: Optional[dict] = None

    @field_validator("vector")
    @classmethod
    def check_dim(cls, v: list[float]) -> list[float]:
        if VECTOR_DIM and len(v) != VECTOR_DIM:
            raise ValueError(f"expected {VECTOR_DIM} dimensions, got {len(v)}")
        return v

    @field_validator("top_k")
    @classmethod
    def check_top_k(cls, v: int) -> int:
        if not (1 <= v <= 10_000):
            raise ValueError("top_k must be between 1 and 10,000")
        return v


class UpsertVector(BaseModel):
    id: str
    values: list[float]
    metadata: Optional[dict] = None

    @field_validator("values")
    @classmethod
    def check_dim(cls, v: list[float]) -> list[float]:
        if VECTOR_DIM and len(v) != VECTOR_DIM:
            raise ValueError(f"expected {VECTOR_DIM} dimensions, got {len(v)}")
        return v


class UpsertRequest(BaseModel):
    vectors: list[UpsertVector]
    namespace: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_index():
    if state.index is None:
        raise HTTPException(status_code=503, detail="Pinecone index not initialized")


def _do_smart_upsert(vectors: list[dict], namespace: Optional[str]) -> dict:
    """
    Called inside asyncio.to_thread — runs in the thread pool.
    Batches by actual JSON byte size to stay within Pinecone's 4 MB gRPC limit.
    """
    batch: list[dict] = []
    batch_bytes = 0
    total = 0

    for vec in vectors:
        vec_bytes = len(json.dumps(vec).encode("utf-8"))
        if batch and (batch_bytes + vec_bytes > _MAX_UPSERT_BYTES or len(batch) >= _MAX_UPSERT_VECTORS):
            state.index.upsert(vectors=batch, namespace=namespace)
            total += len(batch)
            batch, batch_bytes = [], 0
        batch.append(vec)
        batch_bytes += vec_bytes

    if batch:
        state.index.upsert(vectors=batch, namespace=namespace)
        total += len(batch)

    return {"upserted_count": total}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/healthz", tags=["ops"])
async def healthz():
    """Liveness — confirms gRPC connection is initialized."""
    return {
        "status": "ok",
        "index_host": INDEX_HOST,
        "vector_dim": VECTOR_DIM or "unconstrained",
        "thread_pool_size": THREAD_POOL_SIZE,
    }


@app.get("/stats", tags=["ops"])
async def stats():
    """Index stats — vector count, namespaces, dimension."""
    _require_index()
    try:
        t0 = time.perf_counter()
        result = await asyncio.to_thread(state.index.describe_index_stats)
        _emit({"PineconeStatsLatencyMs": (time.perf_counter() - t0) * 1000})
        return result
    except Exception as e:
        logger.exception('"describe_index_stats failed"')
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/query", tags=["vectors"])
async def query(request: QueryRequest):
    """
    Nearest-neighbor query.
    The blocking gRPC call runs in the thread pool via asyncio.to_thread(),
    leaving the event loop free for other concurrent requests.
    """
    _require_index()
    try:
        t0 = time.perf_counter()
        result = await asyncio.to_thread(
            state.index.query,
            vector=request.vector,
            top_k=request.top_k,
            namespace=request.namespace,
            include_values=request.include_values,
            include_metadata=request.include_metadata,
            filter=request.filter,
        )
        pinecone_ms = (time.perf_counter() - t0) * 1000
        _emit({"PineconeQueryLatencyMs": pinecone_ms})
        return result
    except Exception as e:
        logger.exception('"query failed"')
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/upsert", tags=["vectors"])
async def upsert(request: UpsertRequest):
    """
    Upsert vectors. Auto-batches to stay within Pinecone's 4 MB gRPC limit.
    The blocking upsert loop runs in the thread pool via asyncio.to_thread().
    """
    _require_index()
    try:
        vectors = [
            {"id": v.id, "values": v.values, **({"metadata": v.metadata} if v.metadata else {})}
            for v in request.vectors
        ]
        t0 = time.perf_counter()
        result = await asyncio.to_thread(_do_smart_upsert, vectors, request.namespace)
        _emit({"PineconeUpsertLatencyMs": (time.perf_counter() - t0) * 1000})
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception('"upsert failed"')
        raise HTTPException(status_code=502, detail=str(e))


# ---------------------------------------------------------------------------
# Validation errors → clean 422
# ---------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.errors()})
