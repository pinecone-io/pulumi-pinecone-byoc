# saitama

Production FastAPI service for querying a Pinecone BYOC index over gRPC.

A single `PineconeGRPC` client and `Index` connection are created at startup and shared
across all requests — no reconnection overhead per request.

## Setup

`.env` is already configured. Optional overrides:

```
PINECONE_API_KEY=...
INDEX_HOST=https://your-index.svc.your-env.byoc.pinecone.io
VECTOR_DIM=1024        # enables dimension validation on query/upsert (0 = skip)
```

## Run locally

```bash
# with docker-compose (recommended)
docker-compose up --build

# or directly
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/healthz` | Liveness check — confirms gRPC connection is up |
| `GET`  | `/stats` | Index stats (vector count, namespaces, dimension) |
| `POST` | `/query` | Nearest-neighbor query |
| `POST` | `/upsert` | Upsert vectors (auto-batches to stay within Pinecone limits) |
| `GET`  | `/docs` | Swagger UI |

## Example requests

**Health check**
```bash
curl http://localhost:8000/healthz
```

**Index stats**
```bash
curl http://localhost:8000/stats
```

**Query**
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "vector": [0.1, 0.2, ...],
    "top_k": 10,
    "include_metadata": true
  }'
```

**Query with metadata filter**
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "vector": [0.1, 0.2, ...],
    "top_k": 5,
    "filter": {"department": {"$eq": "Women"}}
  }'
```

**Upsert**
```bash
curl -X POST http://localhost:8000/upsert \
  -H "Content-Type: application/json" \
  -d '{
    "vectors": [
      {"id": "vec-1", "values": [0.1, 0.2, ...], "metadata": {"department": "Women"}}
    ]
  }'
```

## Query request fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `vector` | `float[]` | required | Query vector |
| `top_k` | `int` | `10` | Number of results (1–10,000) |
| `namespace` | `string` | `null` | Pinecone namespace |
| `include_values` | `bool` | `false` | Return vector values in results |
| `include_metadata` | `bool` | `true` | Return metadata in results |
| `filter` | `object` | `null` | Metadata filter |

## Enterprise deployment (ECS on EC2)

`saitama.sh` provisions a full production stack on AWS:

| Component | Detail |
|-----------|--------|
| Instance type | `c6in.large` (network-optimized, ENA Express) |
| ECS cluster | EC2 launch type with Container Insights enabled |
| Capacity provider | Managed scaling, `targetCapacity=80%`, `instanceWarmup=90s`, managed draining |
| ALB | `least_outstanding_requests` routing, `/healthz` health check |
| Tasks | `awsvpc` networking, 1 vCPU / 2 GB RAM per task, spread across AZs |
| Uvicorn | Single worker per task (`--workers 1 --timeout-graceful-shutdown 90`) |
| Metrics | CloudWatch EMF — `PineconeQueryLatencyMs`, `PineconeUpsertLatencyMs`, `TotalRequestLatencyMs` |

### Deploy commands

```bash
# Full deploy (ECR + EC2 + ASG + ALB + ECS service)
./saitama.sh deploy

# Check service health and ALB target status
./saitama.sh status

# Tail live logs
./saitama.sh logs

# Scale to 12 tasks
./saitama.sh scale 12

# Stop all tasks (keep infra)
./saitama.sh kill

# Tear down everything
./saitama.sh destroy
```

### Configuration (.env or env vars)

| Variable | Default | Description |
|----------|---------|-------------|
| `PINECONE_API_KEY` | required | Pinecone API key |
| `INDEX_HOST` | required | Index hostname |
| `VECTOR_DIM` | `0` | Dimension validation (0 = skip) |
| `THREAD_POOL_SIZE` | `100` | gRPC thread pool size |
| `INSTANCE_TYPE` | `c6in.large` | EC2 instance type |
| `TASK_COUNT` | `6` | Desired ECS task count |
| `ASG_MIN` / `ASG_MAX` | `3` / `12` | EC2 instance count bounds |
| `CAPACITY_TARGET` | `80` | Managed scaling target % |

### Architecture note

One uvicorn worker per ECS task. Each worker holds a single long-lived gRPC connection
to Pinecone with keepalive pings (20s interval) to prevent AWS NLB/ALB from silently
dropping idle connections. Blocking gRPC calls are offloaded to an explicit
`ThreadPoolExecutor` (100 threads) via `asyncio.to_thread()`, keeping the event loop
free for concurrent requests.
