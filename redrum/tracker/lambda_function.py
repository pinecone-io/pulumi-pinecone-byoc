"""
Pinecone BYOC — freshness tracker Lambda.

Two modes dispatched by event shape:

  Stream mode (default):
    Triggered by DynamoDB Streams on INSERT. Polls index.fetch() until the
    vector is visible, then records latency back to DynamoDB.

  Probe mode (event: {"mode": "probe"}):
    Direct invoked by freshness.py --lambda-probe. Upserts one vector every
    PROBE_UPSERT_INTERVAL seconds for PROBE_DURATION seconds, polls every
    PROBE_POLL_INTERVAL seconds until seen, and returns raw samples as JSON.
"""

import os
import json
import time
import uuid
import statistics
import boto3
import numpy as np
from decimal import Decimal
from pinecone import Pinecone
from sklearn.preprocessing import normalize

INDEX_HOST       = os.environ["INDEX_HOST"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
TABLE_NAME       = os.environ.get("DYNAMO_TABLE", "redrum-freshness")
VECTOR_DIM       = int(os.environ.get("VECTOR_DIM", "1024"))

# stream-mode config
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL_SECONDS", "0.5"))
TIMEOUT       = float(os.environ.get("TIMEOUT_SECONDS", "60"))

# probe-mode config
PROBE_DURATION        = float(os.environ.get("PROBE_DURATION_SECONDS", "180"))
PROBE_UPSERT_INTERVAL = float(os.environ.get("PROBE_UPSERT_INTERVAL_SECONDS", "2"))
PROBE_POLL_INTERVAL   = float(os.environ.get("PROBE_POLL_INTERVAL_SECONDS", "0.01"))
PROBE_VECTOR_TIMEOUT  = float(os.environ.get("PROBE_VECTOR_TIMEOUT_SECONDS", "10"))

pc    = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(host=INDEX_HOST)
ddb   = boto3.resource("dynamodb")
table = ddb.Table(TABLE_NAME)


# ---------------------------------------------------------------------------
# Stream mode
# ---------------------------------------------------------------------------

def handler(event, context):
    if event.get("mode") == "probe":
        return probe_handler(event, context)

    for record in event.get("Records", []):
        if record["eventName"] != "INSERT":
            continue

        new        = record["dynamodb"]["NewImage"]
        vector_id  = new["id"]["S"]
        written_at = Decimal(new["written_at"]["N"])

        print(f"[tracker] checking id={vector_id}", flush=True)

        deadline = time.time() + TIMEOUT
        seen_at  = None

        while time.time() < deadline:
            try:
                result = index.fetch(ids=[vector_id])
                if vector_id in (result.vectors or {}):
                    seen_at = Decimal(str(time.time()))
                    break
            except Exception as e:
                print(f"[tracker] fetch error: {e}", flush=True)
            time.sleep(POLL_INTERVAL)

        if seen_at:
            latency_ms = int((seen_at - written_at) * 1000)
            table.update_item(
                Key={"id": vector_id},
                UpdateExpression="SET seen_at = :s, latency_ms = :l, #st = :ok",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":s": seen_at, ":l": latency_ms, ":ok": "seen"},
            )
            print(f"[tracker] seen id={vector_id} latency={latency_ms}ms", flush=True)
        else:
            table.update_item(
                Key={"id": vector_id},
                UpdateExpression="SET #st = :t",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":t": "timeout"},
            )
            print(f"[tracker] timeout id={vector_id}", flush=True)


# ---------------------------------------------------------------------------
# Probe mode
# ---------------------------------------------------------------------------

def probe_handler(event, context):
    duration = float(event.get("duration_seconds", PROBE_DURATION))
    upsert_interval = float(event.get("upsert_interval_seconds", PROBE_UPSERT_INTERVAL))
    poll_interval   = float(event.get("poll_interval_seconds", PROBE_POLL_INTERVAL))
    vector_timeout  = float(event.get("vector_timeout_seconds", PROBE_VECTOR_TIMEOUT))

    print(f"[probe] starting — duration={duration}s upsert_every={upsert_interval}s poll={int(poll_interval*1000)}ms", flush=True)

    samples   = []
    timeouts  = 0
    run_until = time.time() + duration
    n         = 0

    while time.time() < run_until:
        vid = str(uuid.uuid4())
        vec = normalize(np.random.randn(1, VECTOR_DIM).astype("float32"), norm="l2")[0].tolist()

        t_upsert = time.time()
        index.upsert(vectors=[{
            "id": vid,
            "values": vec,
            "metadata": {"source": "probe", "written_at": t_upsert, "dim": VECTOR_DIM},
        }])
        t0 = time.time()  # clock starts after Pinecone ACKs the write

        deadline = t0 + vector_timeout
        seen = False
        while time.time() < deadline:
            try:
                if vid in (index.fetch(ids=[vid]).vectors or {}):
                    latency_ms = int((time.time() - t0) * 1000)
                    samples.append(latency_ms)
                    seen = True
                    break
            except Exception as e:
                print(f"[probe] fetch error: {e}", flush=True)
            time.sleep(poll_interval)

        n += 1
        if seen:
            print(f"[probe] {n} id={vid[:8]} latency={latency_ms}ms", flush=True)
        else:
            timeouts += 1
            print(f"[probe] {n} id={vid[:8]} timeout (>{vector_timeout}s)", flush=True)

        # sleep for the remainder of the upsert interval
        elapsed = time.time() - t0
        remaining = upsert_interval - elapsed
        if remaining > 0 and time.time() + remaining < run_until:
            time.sleep(remaining)

    result = {
        "samples":    samples,
        "timeouts":   timeouts,
        "count":      len(samples),
    }
    if samples:
        result["p50_ms"] = int(statistics.median(samples))
        result["min_ms"] = min(samples)
        result["max_ms"] = max(samples)
        if len(samples) >= 20:
            qs = statistics.quantiles(samples, n=100)
            result["p95_ms"] = int(qs[94])
            result["p99_ms"] = int(qs[98])

    print(f"[probe] done — {len(samples)} samples, {timeouts} timeouts, p50={result.get('p50_ms')}ms", flush=True)
    return result
