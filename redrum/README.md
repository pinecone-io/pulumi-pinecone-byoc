# redrum

Load generation tools for Pinecone BYOC. Continuously writes vectors and runs queries against an index to simulate traffic.

## Setup

Create a `.env` file in this folder (never committed to git):

```
PINECONE_API_KEY=your_key
INDEX_HOST=https://your-index.svc.your-env.byoc.pinecone.io

# Optional overrides (these are the defaults)
VECTOR_DIM=1024
WRITE_COUNT=200
QUERY_COUNT=10
TOP_K=10
MIN_SLEEP_SECONDS=60
MAX_SLEEP_SECONDS=600
```

## Commands

```bash
./redrum.sh deploy               # build images, push to ECR, start ECS services
./redrum.sh status               # show running/desired task counts for each service
./redrum.sh logs                 # tail live logs from both writer and querier
./redrum.sh logs writer          # tail writer only
./redrum.sh logs querier         # tail querier only
./redrum.sh kill                 # stop both services (keeps AWS resources, easy to restart)
./redrum.sh destroy              # stop services AND delete all AWS resources (ECR, ECS, logs)
```

## Scaling instances

Set `WRITER_COUNT` and `QUERIER_COUNT` in your `.env` or inline before running deploy:

```bash
# 5 queriers, 1 writer
QUERIER_COUNT=5 WRITER_COUNT=1 ./redrum.sh deploy

# or add to .env
WRITER_COUNT=1
QUERIER_COUNT=5
```

Each count maps directly to ECS desired task count — all instances share the same config and log to the same `/redrum` CloudWatch group.

## What it does

| Container | Behavior |
|-----------|----------|
| `redrum-writer` | Wakes up every 1–10 min (random), upserts `WRITE_COUNT` random vectors |
| `redrum-querier` | Wakes up every 1–10 min (random), runs `QUERY_COUNT` nearest-neighbor queries |

Both containers run indefinitely on ECS Fargate until you `kill` or `destroy` them.

## Other scripts

| Script | Description |
|--------|-------------|
| `mk_vectors.py` | Generates 10M random 1024-dim vectors and uploads them to S3 as parquet files for bulk import |
| `query_vectors.py` | One-shot query script — generates a single query vector and prints top-K results to the terminal |

## AWS resources created by deploy

- ECR repositories: `redrum-writer`, `redrum-querier`
- ECS cluster: `redrum`
- ECS services: `redrum-writer`, `redrum-querier` (Fargate, 0.5 vCPU / 1 GB each)
- CloudWatch log group: `/redrum`
- IAM role: `redrumTaskExecutionRole`
- Security group: `redrum-tasks`
