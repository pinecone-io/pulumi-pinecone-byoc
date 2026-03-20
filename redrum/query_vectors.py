"""Query the Pinecone BYOC index with a random vector."""

import os
import numpy as np
from sklearn.datasets import make_blobs
from sklearn.preprocessing import normalize
from pinecone import Pinecone

# --- CONFIGURATION ---
INDEX_HOST = "https://1024-random-pricing-kfq3ti7.svc.preprod-aws-us-east-1-4015.byoc.pinecone.io"
VECTOR_DIM = 1024
TOP_K = 10

# Optional: filter results by metadata (set to None to disable)
METADATA_FILTER = None
# Example filters:
# METADATA_FILTER = {"department": {"$eq": "Women"}}
# METADATA_FILTER = {"price": {"$lt": 50.0}}
# METADATA_FILTER = {"condition": {"$in": ["New", "Like New"]}}

# --- GENERATE QUERY VECTOR ---
# Uses the same distribution as mk_vectors.py (normalized, clustered)
print("Generating query vector...")
centers, _ = make_blobs(n_samples=10, n_features=VECTOR_DIM, centers=10, random_state=99)
centers = normalize(centers, norm="l2")

query_vec, _ = make_blobs(n_samples=1, n_features=VECTOR_DIM, centers=centers, cluster_std=0.5, random_state=7)
query_vec = normalize(query_vec, norm="l2").astype("float32")[0].tolist()

# --- QUERY ---
api_key = os.environ.get("PINECONE_API_KEY")
if not api_key:
    raise ValueError("Set PINECONE_API_KEY environment variable")

pc = Pinecone(api_key=api_key)
index = pc.Index(host=INDEX_HOST)

print(f"Querying index at {INDEX_HOST}")
print(f"Top-K: {TOP_K}, Filter: {METADATA_FILTER}\n")

results = index.query(
    vector=query_vec,
    top_k=TOP_K,
    include_metadata=True,
    filter=METADATA_FILTER,
)

# --- PRINT RESULTS ---
print(f"{'Rank':<6} {'Score':<10} {'ID':<38} {'Department':<10} {'Condition':<12} {'Price':<8} {'Brand'}")
print("-" * 110)
for i, match in enumerate(results["matches"], 1):
    meta = match.get("metadata") or {}
    print(
        f"{i:<6} {match['score']:<10.6f} {match['id']:<38} "
        f"{str(meta.get('department', '')):<10} "
        f"{str(meta.get('condition', '')):<12} "
        f"{str(meta.get('price', '')):<8} "
        f"{str(meta.get('brand', ''))}"
    )
