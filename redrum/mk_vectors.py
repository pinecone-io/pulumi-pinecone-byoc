import pandas as pd
import numpy as np
import uuid
import random
import json
import os
import pyarrow as pa
import pyarrow.parquet as pq
import s3fs
import concurrent.futures
from sklearn.datasets import make_blobs
from sklearn.preprocessing import normalize
import time

# --- CONFIGURATION ---
TOTAL_RECORDS = 10_000_000
FILES_COUNT = 10
RECORDS_PER_FILE = 1_000_000
CHUNK_SIZE = 10_000
VECTOR_DIM = 1024
S3_BUCKET = "test--pc-bulk-import"  # <--- UPDATE THIS
S3_PREFIX = "10MM_Data_1024"
MAX_WORKERS = os.cpu_count() or 4 

# --- S3 CHECKER ---
def get_existing_file_indices():
    """Checks S3 for files that are already finished."""
    s3 = s3fs.S3FileSystem(anon=False)
    # List all files in the folder
    files = s3.glob(f"{S3_BUCKET}/{S3_PREFIX}/part_*.parquet")
    
    # Extract the index number from the filename (e.g. part_0012.parquet -> 12)
    existing_indices = set()
    for f in files:
        try:
            # Assumes format .../part_XXXX.parquet
            fname = f.split('/')[-1]
            idx = int(fname.split('_')[1].split('.')[0])
            existing_indices.add(idx)
        except:
            pass
    return existing_indices

# --- GLOBAL SETUP ---
print("Generating global cluster centers...")
global_centers, _ = make_blobs(n_samples=500, n_features=VECTOR_DIM, centers=500, random_state=42)
global_centers = normalize(global_centers, norm='l2')

# Static Pools
departments = ["Men", "Women", "Kids", "Home", "Vintage", "Beauty"]
conditions = ["New", "Like New", "Good", "Fair", "Poor", "Refurbished", "Damaged"]
availabilities = ["In Stock", "Out of Stock", "Pre-order", "Backorder", "Discontinued", "Limited", "Coming Soon"]
brands_pool = [f"Brand_{i}" for i in range(29_977)]
colors_pool = [f"Color_{i}" for i in range(15)]
categories = [f"Cat_{i}" for i in range(114)]
sub_categories = [f"SubCat_{i}" for i in range(633)]

schema = pa.schema([
    ('id', pa.string()),
    ('values', pa.list_(pa.float32())),
    ('metadata', pa.string())
])

def generate_and_upload_file(file_idx):
    s3 = s3fs.S3FileSystem(anon=False)
    s3_path = f"{S3_BUCKET}/{S3_PREFIX}/part_{file_idx:04d}.parquet"

    # --- THE SAFETY CHECK ---
    # We double-check here just in case multiple workers race (rare but safe)
    if s3.exists(s3_path):
        print(f"[{file_idx}] Exists. Skipping.")
        return f"File {file_idx} Skipped"

    print(f"[{file_idx}] Starting {s3_path}...")

    try:
        with pq.ParquetWriter(s3_path, schema, compression='snappy', filesystem=s3) as writer:
            chunks_needed = RECORDS_PER_FILE // CHUNK_SIZE
            
            for chunk_idx in range(chunks_needed):
                current_seed = (file_idx * 10000) + chunk_idx
                
                # Data Generation Logic (Same as before)
                vectors_array, _ = make_blobs(
                    n_samples=CHUNK_SIZE, n_features=VECTOR_DIM, 
                    centers=global_centers, cluster_std=0.5, random_state=current_seed
                )
                vectors_array = normalize(vectors_array, norm='l2').astype('float32')

                ids = [str(uuid.uuid4()) for _ in range(CHUNK_SIZE)]
                
                np.random.seed(current_seed)
                dept_col = np.random.choice(departments, CHUNK_SIZE)
                cond_col = np.random.choice(conditions, CHUNK_SIZE)
                avail_col = np.random.choice(availabilities, CHUNK_SIZE)
                brand_col = np.random.choice(brands_pool, CHUNK_SIZE)
                cat_col = np.random.choice(categories, CHUNK_SIZE)
                sub_cat_col = np.random.choice(sub_categories, CHUNK_SIZE)
                price_col = np.random.lognormal(mean=3.5, sigma=0.8, size=CHUNK_SIZE).round(2)
                seller_col = np.random.randint(1, 154169165, size=CHUNK_SIZE)
                size_col = [f"Sz_{np.random.randint(0, 11217027)}" for _ in range(CHUNK_SIZE)]

                random.seed(current_seed)
                color_col = [random.sample(colors_pool, k=random.randint(1, 2)) for _ in range(CHUNK_SIZE)]

                meta_df = pd.DataFrame({
                    "department": dept_col, "availability": avail_col, "condition": cond_col,
                    "color": color_col, "category": cat_col, "sub_category": sub_cat_col,
                    "brand": brand_col, "price": price_col, "size": size_col, "seller_id": seller_col
                })
                meta_json = meta_df.apply(lambda row: json.dumps(row.to_dict(), ensure_ascii=False), axis=1)

                batch_df = pd.DataFrame({
                    "id": ids, "values": list(vectors_array), "metadata": meta_json
                })
                
                table = pa.Table.from_pandas(batch_df, schema=schema)
                writer.write_table(table)
                del vectors_array, meta_df, batch_df, table, ids

        return f"File {file_idx} Success"

    except Exception as e:
        # If it fails, we try to delete the partial file so it doesn't look "Done" next time
        try:
            s3.rm(s3_path)
        except:
            pass
        print(f"[{file_idx}] FAILED: {e}")
        return f"File {file_idx} Failed"

if __name__ == "__main__":
    # 1. CHECK WHAT IS ALREADY DONE
    print("Checking S3 for existing files...")
    completed_indices = get_existing_file_indices()
    print(f"Found {len(completed_indices)} existing files. These will be skipped.")

    # 2. CREATE LIST OF WORK ONLY FOR MISSING FILES
    all_indices = set(range(FILES_COUNT))
    remaining_indices = list(all_indices - completed_indices)
    remaining_indices.sort() # Keep order nice

    if not remaining_indices:
        print("All files are already done!")
    else:
        print(f"Starting processing for {len(remaining_indices)} remaining files with {MAX_WORKERS} workers...")
        
        with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(generate_and_upload_file, i) for i in remaining_indices]
            
            for future in concurrent.futures.as_completed(futures):
                pass