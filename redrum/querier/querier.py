"""
Pinecone BYOC — continuous querier.
Runs QUERY_COUNT queries every MIN_SLEEP_SECONDS–MAX_SLEEP_SECONDS seconds.
"""

import os
import time
import random
import numpy as np
from sklearn.preprocessing import normalize
from pinecone import Pinecone

INDEX_HOST        = os.environ["INDEX_HOST"]
PINECONE_API_KEY  = os.environ["PINECONE_API_KEY"]
VECTOR_DIM        = int(os.environ.get("VECTOR_DIM", "1024"))
QUERY_COUNT       = int(os.environ.get("QUERY_COUNT", "10"))
TOP_K             = int(os.environ.get("TOP_K", "10"))
MIN_SLEEP         = int(os.environ.get("MIN_SLEEP_SECONDS", "60"))
MAX_SLEEP         = int(os.environ.get("MAX_SLEEP_SECONDS", "600"))

pc    = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(host=INDEX_HOST)

print(f"[querier] started — dim={VECTOR_DIM} query_count={QUERY_COUNT} top_k={TOP_K} sleep={MIN_SLEEP}-{MAX_SLEEP}s", flush=True)

while True:
    sleep_sec = random.randint(MIN_SLEEP, MAX_SLEEP)
    print(f"[querier] sleeping {sleep_sec}s", flush=True)
    time.sleep(sleep_sec)

    try:
        for i in range(QUERY_COUNT):
            vec = normalize(np.random.randn(1, VECTOR_DIM).astype("float32"), norm="l2")[0].tolist()
            result = index.query(vector=vec, top_k=TOP_K)
            top = result["matches"][0] if result["matches"] else {}
            print(f"[querier] query {i+1}/{QUERY_COUNT} top_id={top.get('id','?')} score={top.get('score',0):.6f}", flush=True)
        print(f"[querier] completed {QUERY_COUNT} queries", flush=True)
    except Exception as e:
        body = getattr(getattr(e, "body", None), "decode", lambda: str(getattr(e, "body", "")))()
        print(f"[querier] ERROR: {e} | body={body}", flush=True)
