"""
Pinecone BYOC — continuous vector writer.
Writes WRITE_COUNT vectors every MIN_SLEEP_SECONDS–MAX_SLEEP_SECONDS seconds.

When /redrum/freshness_enabled is "true" in SSM, batch-writes each vector's
{id, written_at} to DynamoDB so the tracker Lambda can measure read latency.
"""

import json
import os
import random
import time
import uuid
from decimal import Decimal

import boto3
import numpy as np
from pinecone import Pinecone
from sklearn.preprocessing import normalize

INDEX_HOST        = os.environ["INDEX_HOST"]
PINECONE_API_KEY  = os.environ["PINECONE_API_KEY"]
AWS_REGION        = os.environ.get("AWS_REGION", "us-east-1")
VECTOR_DIM        = int(os.environ.get("VECTOR_DIM", "1024"))
WRITE_COUNT       = int(os.environ.get("WRITE_COUNT", "200"))
MIN_SLEEP         = int(os.environ.get("MIN_SLEEP_SECONDS", "60"))
MAX_SLEEP         = int(os.environ.get("MAX_SLEEP_SECONDS", "600"))
DYNAMO_TABLE      = os.environ.get("DYNAMO_TABLE", "redrum-freshness")
SSM_FLAG_PATH     = os.environ.get("SSM_FLAG_PATH", "/redrum/freshness_enabled")

# Pinecone hard limits
_MAX_BYTES   = 4_000_000
_MAX_VECTORS = 100

ssm   = boto3.client("ssm", region_name=AWS_REGION)
ddb   = boto3.resource("dynamodb", region_name=AWS_REGION)
table = ddb.Table(DYNAMO_TABLE)


def freshness_enabled() -> bool:
    try:
        resp = ssm.get_parameter(Name=SSM_FLAG_PATH)
        return resp["Parameter"]["Value"].strip().lower() == "true"
    except Exception:
        return False


def smart_upsert(index, vectors: list[dict]) -> int:
    batch: list[dict] = []
    batch_bytes = 0
    total = 0

    for vec in vectors:
        vec_bytes = len(json.dumps(vec).encode("utf-8"))
        if batch and (batch_bytes + vec_bytes > _MAX_BYTES or len(batch) >= _MAX_VECTORS):
            index.upsert(vectors=batch)
            print(f"[writer] batch upserted {len(batch)} vectors ({batch_bytes:,} bytes)", flush=True)
            total += len(batch)
            batch = []
            batch_bytes = 0
        batch.append(vec)
        batch_bytes += vec_bytes

    if batch:
        index.upsert(vectors=batch)
        print(f"[writer] batch upserted {len(batch)} vectors ({batch_bytes:,} bytes)", flush=True)
        total += len(batch)

    return total


def record_freshness(vector_ids: list[str], written_at: float):
    """Batch-write pending freshness records to DynamoDB (25 per call)."""
    ts = Decimal(str(written_at))
    items = [
        {"id": vid, "written_at": ts, "status": "pending"}
        for vid in vector_ids
    ]
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    print(f"[writer] freshness: recorded {len(items)} ids to DynamoDB", flush=True)


pc    = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(host=INDEX_HOST)

print(f"[writer] started — dim={VECTOR_DIM} write_count={WRITE_COUNT} sleep={MIN_SLEEP}-{MAX_SLEEP}s", flush=True)

while True:
    sleep_sec = random.randint(MIN_SLEEP, MAX_SLEEP)
    print(f"[writer] sleeping {sleep_sec}s", flush=True)
    time.sleep(sleep_sec)

    try:
        vecs = normalize(np.random.randn(WRITE_COUNT, VECTOR_DIM).astype("float32"), norm="l2")
        ids  = [str(uuid.uuid4()) for _ in range(WRITE_COUNT)]
        vectors = [
            {
                "id": ids[i],
                "values": vecs[i].tolist(),
                "metadata": {"source": "writer", "written_at": written_at, "dim": VECTOR_DIM},
            }
            for i in range(WRITE_COUNT)
        ]

        written_at = time.time()
        total = smart_upsert(index, vectors)
        print(f"[writer] done — {total} vectors upserted total", flush=True)

        if freshness_enabled():
            record_freshness(ids, written_at)
    except Exception as e:
        body = getattr(getattr(e, "body", None), "decode", lambda: str(getattr(e, "body", "")))()
        print(f"[writer] ERROR: {e} | body={body}", flush=True)
