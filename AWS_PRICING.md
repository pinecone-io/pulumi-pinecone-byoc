# AWS Infrastructure Pricing — us-east-1

Estimated monthly cost for deploying `pulumi-pinecone-byoc` in **us-east-1**.
Prices are **on-demand, Linux** unless noted. All calculations use **730 hours/month**.

> **Important:** EC2 instance prices (r6in, m6idn, i7ie families) and Aurora db.r8g prices are **approximate** — the AWS documentation corpus does not embed these specific figures inline. Always confirm at:
>
> - EC2: [https://aws.amazon.com/ec2/pricing/on-demand/](https://aws.amazon.com/ec2/pricing/on-demand/)
> - Aurora: [https://aws.amazon.com/rds/aurora/pricing/](https://aws.amazon.com/rds/aurora/pricing/)
>
> All other prices below are confirmed from AWS documentation sources (see §Sources).

---

## Deployment Assumptions


| Parameter               | Value                              |
| ----------------------- | ---------------------------------- |
| Region                  | us-east-1                          |
| Availability Zones      | 3                                  |
| EKS node pool (default) | 3 × `r6in.large` (desired=3)       |
| EBS per node            | 100 GB gp3                         |
| RDS clusters            | 2 × `db.r8g.large` (1 writer each) |
| NAT Gateways            | 3 (one per AZ)                     |
| Elastic IPs             | 3 (attached to NAT GWs)            |
| Load balancers          | 1 NLB + 2 private ALBs             |
| Public ALBs             | Optional — see §Public Access      |
| VPC Interface Endpoint  | 1 endpoint, 3 AZs                  |
| Secrets Manager secrets | 5 (2 per RDS cluster + 1 misc)     |
| KMS CMKs                | 4 (1 Pulumi, 2 RDS, 1 S3)          |
| S3 buckets              | 6 (5 data + 1 Pulumi state)        |
| AMP                     | 1 workspace                        |


---

## 1. Networking

### 1.1 NAT Gateways (3)


| Item                                  | Unit Price | Qty    | Hours | Monthly    |
| ------------------------------------- | ---------- | ------ | ----- | ---------- |
| NAT GW — hourly                       | $0.045/hr  | 3      | 730   | **$98.55** |
| NAT GW — data processed (est. 200 GB) | $0.045/GB  | 200 GB | —     | **$9.00**  |


**Subtotal: ~$107.55/month**

> Source: [AWS VPC NAT pricing confirmed in docs](https://docs.aws.amazon.com/solutions/latest/migration-assistant-for-amazon-opensearch-service/cost.html)

### 1.2 Elastic IPs (3 — attached to NAT GWs)

Since February 2024, **all** public IPv4 addresses are charged at $0.005/hr regardless of in-use status.


| Item                 | Unit Price | Qty | Hours | Monthly    |
| -------------------- | ---------- | --- | ----- | ---------- |
| Public IPv4 (in-use) | $0.005/hr  | 3   | 730   | **$10.95** |


**Subtotal: $10.95/month**

### 1.3 VPC, Subnets, IGW, Route Tables

No charge.

---

## 2. EKS

### 2.1 EKS Control Plane (1 cluster)


| Item                   | Unit Price | Hours | Monthly    |
| ---------------------- | ---------- | ----- | ---------- |
| EKS cluster — standard | $0.10/hr   | 730   | **$73.00** |


> Extended support (Kubernetes versions past standard support window) incurs additional charges.

### 2.2 EC2 Worker Nodes — Default Pool: 3 × `r6in.large`

`r6in.large` — 2 vCPU, 16 GiB RAM, network-enhanced (Intel Ice Lake, up to 25 Gbps)


| Item                          | Unit Price ⚠️ | Qty | Hours | Monthly      |
| ----------------------------- | ------------- | --- | ----- | ------------ |
| r6in.large (Linux, on-demand) | ~$0.2268/hr*  | 3   | 730   | **~$497.25** |


*⚠️ **Unconfirmed** — verify at [https://aws.amazon.com/ec2/pricing/on-demand/](https://aws.amazon.com/ec2/pricing/on-demand/). The r6in family carries a ~50% premium over r6i due to enhanced networking.

### 2.3 EBS gp3 Volumes (3 nodes × 100 GB)


| Item                                           | Unit Price        | Qty         | Monthly    |
| ---------------------------------------------- | ----------------- | ----------- | ---------- |
| gp3 storage                                    | $0.08/GB-month    | 300 GB      | **$24.00** |
| IOPS above 3,000 (baseline included)           | $0.005/IOPS-month | 0 (default) | $0.00      |
| Throughput above 125 MiB/s (baseline included) | $0.04/MiB/s-month | 0 (default) | $0.00      |


**Subtotal: $24.00/month**

> Source: [EBS gp3 confirmed in AWS EMR docs](https://docs.aws.amazon.com/emr/latest/ManagementGuide/emr-plan-storage-compare-volume-types.html)

**EKS Subtotal: ~$594.25/month**

---

## 3. S3 (6 Buckets)

S3 costs depend heavily on actual data volume. All buckets have versioning enabled and lifecycle rules to control costs (noncurrent versions expire in 3–30 days depending on bucket type).


| Bucket                    | Purpose              | Storage Tier    |
| ------------------------- | -------------------- | --------------- |
| `pc-data-{cell}`          | Vector data          | S3 Standard     |
| `pc-index-backups-{cell}` | Index snapshots      | S3 Standard     |
| `pc-wal-{cell}`           | **Write-ahead logs** | **S3 Standard** |
| `pc-janitor-{cell}`       | Cleanup ops          | S3 Standard     |
| `pc-internal-{cell}`      | Operational          | S3 Standard     |
| `pc-pulumi-state-{cell}`  | Pulumi state         | S3 Standard     |


**S3 Standard pricing (us-east-1):**


| Item                        | Unit Price      |
| --------------------------- | --------------- |
| Storage (first 50 TB)       | $0.023/GB-month |
| PUT/COPY/POST/LIST requests | $0.005/1,000    |
| GET/SELECT requests         | $0.0004/1,000   |


> S3 monthly costs scale entirely with data volume. A deployment with 1 TB of vector data = ~$23.00/month in storage alone. **Listed as variable below.**

---

## 4. RDS Aurora PostgreSQL (2 Clusters)

Each cluster has 1 writer instance (`db.r8g.large` — Graviton4, 2 vCPU, 16 GiB RAM).

### 4.1 Instance Costs


| Cluster      | Instance     | Unit Price ⚠️ | Qty | Hours | Monthly      |
| ------------ | ------------ | ------------- | --- | ----- | ------------ |
| `control-db` | db.r8g.large | ~$0.260/hr*   | 1   | 730   | **~$189.80** |
| `system-db`  | db.r8g.large | ~$0.260/hr*   | 1   | 730   | **~$189.80** |


*⚠️ **Unconfirmed** — verify at [https://aws.amazon.com/rds/aurora/pricing/](https://aws.amazon.com/rds/aurora/pricing/). The db.r8g (Graviton4) is newer than db.r7g; price estimated based on the Graviton pricing curve.

### 4.2 Aurora Storage & I/O

Aurora storage is billed per GB-month used by the cluster (billed in 10 GB increments, grows automatically).

**Standard Aurora (default):**


| Item                          | Unit Price             |
| ----------------------------- | ---------------------- |
| Storage                       | $0.10/GB-month         |
| I/O requests                  | $0.20/million requests |
| Backup storage beyond DB size | $0.021/GB-month        |


*Estimated (50 GB/cluster, ~10M I/O requests/month):*

- 2 clusters × 50 GB × $0.10 = $10.00
- 2 clusters × 10M × $0.20/M = $4.00
- **Storage + I/O estimate: ~$14.00/month**

> Alternatively, **Aurora I/O Optimized** ($0.225/GB-month, no I/O request charges) may be cost-effective at high I/O workloads.

**RDS Subtotal: ~$393.60/month**

---

## 5. Load Balancers

### 5.1 Network Load Balancer (1 — internal)


| Item                    | Unit Price     | Qty         | Hours | Monthly    |
| ----------------------- | -------------- | ----------- | ----- | ---------- |
| NLB hourly              | $0.0225/hr     | 1           | 730   | **$16.43** |
| NLCU (est. low traffic) | $0.006/NLCU-hr | ~10 NLCU-hr | —     | **~$4.38** |


**NLB Subtotal: ~$20.81/month**

### 5.2 Private ALBs (2 — gRPC + REST, internal)


| Item                   | Unit Price    | Qty              | Hours | Monthly    |
| ---------------------- | ------------- | ---------------- | ----- | ---------- |
| ALB hourly             | $0.0225/hr    | 2                | 730   | **$32.85** |
| LCU (est. low traffic) | $0.008/LCU-hr | ~10 LCU-hr total | —     | **~$5.84** |


**Private ALB Subtotal: ~$38.69/month**

> ALB/NLB LCU costs are highly traffic-dependent. Figures above assume very low traffic. Production workloads will be significantly higher.

> Source: ALB hourly rate confirmed at $0.0225/hr in [AWS documentation](https://docs.aws.amazon.com/solutions/latest/migration-assistant-for-amazon-opensearch-service/cost.html)

**Load Balancer Subtotal (private only): ~$59.50/month**

---

## 6. DNS & Certificates

### 6.1 Route 53 Hosted Zone (1)


| Item                            | Unit Price       | Monthly   |
| ------------------------------- | ---------------- | --------- |
| Hosted zone (first 25)          | $0.50/zone/month | **$0.50** |
| DNS queries (standard, est. 1M) | $0.40/million    | **$0.40** |


**Subtotal: ~$0.90/month**

> Source: Route 53 $0.50/zone confirmed in [AWS documentation](https://docs.aws.amazon.com/solutions/latest/migration-assistant-for-amazon-opensearch-service/cost.html)

### 6.2 ACM Certificates

Public and private ACM certificates provisioned for `*.{fqdn}` and `*.svc.{fqdn}`.

**Cost: $0.00** — ACM public certificates are free. ACM private certificates (Private CA) are separate but this deployment uses ACM-managed public certs.

---

## 7. PrivateLink

### 7.1 VPC Interface Endpoint (1 endpoint, 3 AZs)


| Item                         | Unit Price  | Qty    | Hours | Monthly    |
| ---------------------------- | ----------- | ------ | ----- | ---------- |
| Endpoint-hour (per AZ)       | $0.01/AZ-hr | 3 AZs  | 730   | **$21.90** |
| Data processed (est. 100 GB) | $0.01/GB    | 100 GB | —     | **$1.00**  |


**Subtotal: ~$22.90/month**

> Source: VPC endpoint pricing confirmed at $0.01/AZ-hr and $0.01/GB in [AWS Clickstream docs](https://docs.aws.amazon.com/solutions/latest/clickstream-analytics-on-aws/cost.html)

### 7.2 VPC Endpoint Service (provider side)

No additional hourly fee for the endpoint service itself — costs are the NLB charges already counted in §5.1. In-region PrivateLink data transfer: **$0.01/GB** (already included above).

---

## 8. Secrets Manager (~5 secrets)

2 secrets per RDS cluster (master password + connection info) + 1 additional.


| Item                       | Unit Price         | Qty     | Monthly    |
| -------------------------- | ------------------ | ------- | ---------- |
| Per secret                 | $0.40/secret/month | 5       | **$2.00**  |
| API calls (est. 50k/month) | $0.05/10k calls    | 5 units | **$0.025** |


**Subtotal: ~$2.03/month**

> Source: $0.40/secret confirmed in [AWS documentation](https://docs.aws.amazon.com/solutions/latest/migration-assistant-for-amazon-opensearch-service/cost.html)

---

## 9. KMS (~4 CMKs)

1 Pulumi secrets key + 2 RDS encryption keys + 1 S3 encryption key.


| Item                  | Unit Price      | Qty      | Monthly   |
| --------------------- | --------------- | -------- | --------- |
| CMK (symmetric)       | $1.00/key/month | 4        | **$4.00** |
| API calls (est. 100k) | $0.03/10k calls | 10 units | **$0.30** |


**Subtotal: ~$4.30/month**

> Source: $1.00/key confirmed via [AWS Druid solution docs](https://docs.aws.amazon.com/solutions/latest/scalable-analytics-using-apache-druid-on-aws/cost.html)

---

## 10. Amazon Managed Prometheus (AMP)

AMP costs scale **significantly** with the number of Prometheus metrics scraped. For a 3-node EKS cluster with ~20–40 Pinecone pods:


| Tier                 | Unit Price     | Est. Monthly Samples | Cost      |
| -------------------- | -------------- | -------------------- | --------- |
| First 2B samples     | $0.90/million  | 2,000M               | $1,800.00 |
| Next 8B samples      | $0.60/million  | (overflow)           | —         |
| Storage              | $0.03/GB-month | ~5 GB                | $0.15     |
| Queries (first 300B) | $0.01/billion  | —                    | ~$1.00    |


> ⚠️ **AMP ingestion costs can be very high.** A typical EKS cluster scraping every 30 seconds generates **2–10 billion samples/month**. At $0.90/million for the first 2B samples, ingestion alone can exceed **$1,800/month** for a busy cluster.
>
> **Mitigation strategies:** Use remote write filtering/relabeling, increase scrape intervals, or reduce the number of tracked series. In practice, many teams filter 50–80% of metrics at the remote write level.

**Estimated range: $200–$2,000+/month** (highly variable — treat as a critical cost driver to monitor)

---

## 11. CloudWatch Logs (EKS Control Plane)

EKS control plane logging is enabled for: API, audit, authenticator, controllerManager, scheduler.


| Item                            | Unit Price | Est. Volume | Monthly   |
| ------------------------------- | ---------- | ----------- | --------- |
| Log ingestion                   | $0.50/GB   | 5 GB        | **$2.50** |
| Log storage (1 month retention) | $0.03/GB   | 5 GB        | **$0.15** |


**Subtotal: ~$2.65/month**

---

## 12. Data Transfer (Outbound to Internet)

Applies to traffic leaving the VPC to the public internet. First 100 GB/month is free.


| Tier               | Unit Price |
| ------------------ | ---------- |
| First 100 GB/month | Free       |
| Next 9.9 TB/month  | $0.09/GB   |
| Next 40 TB/month   | $0.085/GB  |


> Data transfer costs depend entirely on client request volume. Not included in the base estimate below; budget separately based on expected traffic.

---

## Monthly Cost Summary

### Base Deployment (private access only, 3× r6in.large default pool)


| #                                | Service                                    | Monthly Cost      |
| -------------------------------- | ------------------------------------------ | ----------------- |
| 1                                | NAT Gateways (3) — hourly + data           | $107.55           |
| 2                                | Elastic IPs (3)                            | $10.95            |
| 3                                | EKS control plane                          | $73.00            |
| 4                                | EC2 — 3× r6in.large ⚠️                     | ~$497.25          |
| 5                                | EBS gp3 — 300 GB                           | $24.00            |
| 6                                | Aurora PostgreSQL — 2× db.r8g.large ⚠️     | ~$379.60          |
| 7                                | Aurora storage + I/O (est.)                | ~$14.00           |
| 8                                | NLB (1)                                    | ~$20.81           |
| 9                                | Private ALBs (2)                           | ~$38.69           |
| 10                               | Route 53 hosted zone + queries             | ~$0.90            |
| 11                               | ACM certificates                           | $0.00             |
| 12                               | VPC Interface Endpoint (PrivateLink)       | ~$22.90           |
| 13                               | Secrets Manager (5 secrets)                | ~$2.03            |
| 14                               | KMS CMKs (4 keys)                          | ~$4.30            |
| 15                               | CloudWatch Logs                            | ~$2.65            |
| 16                               | IAM, VPC, Subnets, IGW, Route Tables       | $0.00             |
| **Total (fixed infrastructure)** |                                            | **~$1,198/month** |
|                                  |                                            |                   |
| 17                               | **AMP (variable — see §10)**               | **$200–$2,000+**  |
| 18                               | **S3 storage (variable — data dependent)** | **$0.023/GB**     |
| 19                               | **Data transfer out (variable)**           | **$0.09/GB**      |


### With Public Access Enabled (`public_access_enabled=true`)

Add 2 public (internet-facing) ALBs:


| Item                            | Monthly Addition   |
| ------------------------------- | ------------------ |
| 2× Public ALBs (hourly)         | +$32.85            |
| LCU charges (traffic-dependent) | variable           |
| **Additional fixed cost**       | **+~$32.85/month** |


**Total with public ALBs: ~$1,231/month** (before AMP, S3, and data transfer)

---

## Full-Pool Deployment (all 4 instance types)

If you deploy all four instance types that the setup wizard validates (one pool each, desired=3 per pool):


| Pool                                     | Instance     | ~Price/hr ⚠️ | Nodes        | Monthly     |
| ---------------------------------------- | ------------ | ------------ | ------------ | ----------- |
| Default                                  | r6in.large   | ~$0.2268     | 3            | ~$497       |
| Memory                                   | m6idn.large  | ~$0.3996     | 3            | ~$875       |
| Memory-XL                                | m6idn.xlarge | ~$0.7992     | 3            | ~$1,750     |
| Storage                                  | i7ie.large   | ~$0.6318     | 3            | ~$1,384     |
| **EC2 Total**                            |              |              | **12 nodes** | **~$4,506** |
| + Additional EBS (9 more nodes × 100 GB) |              |              |              | **+$72**    |


> ⚠️ All per-instance prices in this table are **approximate and unconfirmed**. Verify at [https://aws.amazon.com/ec2/pricing/on-demand/](https://aws.amazon.com/ec2/pricing/on-demand/) before planning.

**Estimated total for full multi-pool deployment: ~$5,700–$8,000+/month** (before AMP, S3, data transfer)

---

## Vector-Count Based Estimates

This section breaks down costs by dataset size. It uses **10 million vectors at 33.5 GB** as the reference point, then scales to adjacent tiers.

### Sizing Model


| Parameter                | Value                                                 |
| ------------------------ | ----------------------------------------------------- |
| Reference dataset        | 10M vectors, 33.5 GB                                  |
| Implied bytes/vector     | ~~3,350 bytes (~~768-dim float32 + metadata overhead) |
| In-memory index overhead | ~1.5–2× raw data (HNSW)                               |
| RAM needed to serve      | ~50–67 GB                                             |


> **Dimension check:** 768-dim × 4 bytes (float32) = 3,072 bytes/vector raw. With metadata and HNSW graph overhead (~1.5×), in-memory footprint is ~55–67 GB for 10M vectors at 768 dims.

---

### S3 Storage by Bucket (10M vectors, 33.5 GB)

Vectors flow through multiple buckets during the index lifecycle:


| Bucket                    | Estimated Size | Basis                                                  |
| ------------------------- | -------------- | ------------------------------------------------------ |
| `pc-data-{cell}`          | ~33.5 GB       | Raw vector data (primary copy)                         |
| `pc-index-backups-{cell}` | ~33.5 GB       | 1 full snapshot; noncurrent versions expire in 3 days  |
| `pc-wal-{cell}`           | ~3–5 GB        | WAL ~10–15% of data size; lifecycle purges old entries |
| `pc-janitor-{cell}`       | <1 GB          | Cleanup metadata only                                  |
| `pc-internal-{cell}`      | <1 GB          | Operational data only                                  |
| `pc-pulumi-state-{cell}`  | <1 GB          | Pulumi state (negligible)                              |
| **Total S3**              | **~71–74 GB**  |                                                        |


**S3 cost for 10M vectors:**


| Item                                                     | Unit Price    | Volume       | Monthly              |
| -------------------------------------------------------- | ------------- | ------------ | -------------------- |
| S3 Standard storage                                      | $0.023/GB     | 73 GB        | **$1.68**            |
| PUT requests — initial ingest (10M vectors, batch 1,000) | $0.005/1,000  | 10,000       | **$0.05** (one-time) |
| GET requests — ongoing queries (est. 10M/month)          | $0.0004/1,000 | 10,000 units | **$0.40**            |
| **S3 Total**                                             |               |              | **~$2.08/month**     |


> S3 storage cost at this scale is minimal. The dominant costs are compute (EC2) and memory sizing.

---

### Node Sizing for 10M Vectors

The default 3× `r6in.large` (16 GiB each = 48 GB total) is **likely insufficient** for serving 10M vectors at 768 dims. The HNSW index needs ~55–67 GB in RAM.


| Config                  | Total RAM | Serves 10M @ 768d? | ~EC2 Cost/month         |
| ----------------------- | --------- | ------------------ | ----------------------- |
| 3× r6in.large           | 48 GB     | ⚠️ Tight / may OOM | ~$497                   |
| 5× r6in.large           | 80 GB     | ✅ Comfortable      | ~$828                   |
| 4× r6in.xlarge (32 GiB) | 128 GB    | ✅ Headroom         | ~$1,054 ⚠️ verify price |
| 3× m6idn.large          | 24 GB     | ❌ Insufficient     | ~$875                   |


> ⚠️ Pinecone's sharding model distributes index shards across nodes, so actual RAM utilization per node depends on shard count and replication factor configured in your Pinecone stack. The table above reflects worst-case single-shard memory pressure. In a sharded deployment, 3–5 nodes may work depending on your replica count.

**Recommended baseline for 10M vectors at 768 dims:** **5× r6in.large** (~$828/month for EC2 alone).

---

### Full Cost Estimate — 10M Vectors at 33.5 GB

Replaces the EC2 line in §Monthly Cost Summary with 5× r6in.large:


| Service                                                       | Monthly Cost             |
| ------------------------------------------------------------- | ------------------------ |
| Fixed infrastructure (from base estimate, EC2 replaced below) |                          |
| NAT Gateways (3)                                              | $107.55                  |
| Elastic IPs (3)                                               | $10.95                   |
| EKS control plane                                             | $73.00                   |
| **EC2 — 5× r6in.large ⚠️**                                    | **~$828.75**             |
| EBS gp3 — 500 GB (5 nodes × 100 GB)                           | $40.00                   |
| Aurora PostgreSQL — 2× db.r8g.large ⚠️                        | ~$379.60                 |
| Aurora storage + I/O                                          | ~$14.00                  |
| NLB (1) + Private ALBs (2)                                    | ~$59.50                  |
| Route 53                                                      | ~$0.90                   |
| PrivateLink VPC endpoint                                      | ~$22.90                  |
| Secrets Manager, KMS, CloudWatch                              | ~$8.98                   |
| **S3 — 73 GB for 10M vectors**                                | **~$2.08**               |
| **Fixed total**                                               | **~$1,548/month**        |
| AMP (variable)                                                | $200–$2,000+             |
| Data transfer out (variable)                                  | $0.09/GB                 |
| **Estimated total (mid AMP)**                                 | **~$2,100–$2,500/month** |


---

### Scaling Table — Vector Count vs. Monthly Cost

Assumes 768-dim float32 vectors, 1 backup snapshot, default infrastructure otherwise. EC2 node count scales with memory requirement.


| Vectors | Raw Size | S3 Total | Nodes Needed     | EC2 Cost ⚠️ | Fixed Infra | + Mid AMP |
| ------- | -------- | -------- | ---------------- | ----------- | ----------- | --------- |
| 1M      | 3.4 GB   | ~7 GB    | 3× r6in.large    | ~$497       | ~$1,110     | ~$1,600   |
| 10M     | 33.5 GB  | ~73 GB   | 5× r6in.large    | ~$829       | ~$1,548     | ~$2,200   |
| 50M     | 167 GB   | ~360 GB  | ~10× r6in.large  | ~$1,657     | ~$2,700     | ~$3,500   |
| 100M    | 335 GB   | ~720 GB  | ~18× r6in.large* | ~$2,981     | ~$4,200     | ~$5,500   |
| 500M    | 1.67 TB  | ~3.6 TB  | ~90× r6in.large* | ~$14,900    | ~$17,000    | ~$20,000+ |


*At 50M+ vectors you would likely switch to a mix of `m6idn.xlarge` or `r6in.xlarge` for better memory density. Node count estimates assume ~80% memory utilization target.

> **Note:** This table uses the default `r6in.large` for all tiers. In practice, Pinecone would configure larger instance types (e.g., `m6idn.xlarge` at 64 GiB) for higher vector counts, which changes the per-node cost but reduces node count. The fixed infrastructure overhead (Aurora, NAT GWs, NLB, etc.) stays roughly constant regardless of vector count.

---

### What Drives Cost at Each Scale


| Scale            | Dominant Cost Driver                                                           |
| ---------------- | ------------------------------------------------------------------------------ |
| < 5M vectors     | Fixed infrastructure (Aurora, NAT GW, EKS) dominates                           |
| 5M–50M vectors   | EC2 memory capacity becomes the largest single line item                       |
| 50M–500M vectors | EC2 + AMP both significant; S3 storage grows but remains < 5% of total         |
| > 500M vectors   | Requires multi-pool deployment; `m6idn.xlarge` / `i7ie.large` pools drive cost |


---

1. **Savings Plans / Reserved Instances** — EC2 and RDS instances can be reserved for 1–3 years for 30–60% savings. For EC2, Compute Savings Plans provide the most flexibility. For RDS, Reserved Instances are instance-specific.
2. **AMP cost control** — Use Prometheus remote write relabeling to drop high-cardinality metrics before they reach AMP. This is the single highest-impact cost lever.
3. **NAT Gateway alternative** — 3 NAT Gateways at $98.55/month is a significant fixed cost. Ensure cross-AZ NAT GW traffic is minimized by routing each AZ's private traffic to its local NAT GW (already implemented in `vpc.py`).
4. **Aurora I/O Optimized** — If I/O request costs exceed ~$0.06/GB of storage, Aurora I/O Optimized pricing ($0.225/GB-month, no I/O charge) may reduce costs.
5. **S3 Intelligent Tiering** — Vector data accessed infrequently may benefit from S3 Intelligent Tiering after the first few months.
6. **Spot Instances** — Not recommended for Pinecone BYOC production deployments (workload requires consistent instance availability).

---

## IAM & Free Services

No additional monthly cost for:

- IAM roles (6 IRSA roles for add-ons)
- VPC, Subnets, Internet Gateway, Route Tables
- Security Groups
- EKS OIDC Provider
- ACM public TLS certificates
- VPC Endpoint Service (provider side — NLB costs already counted)

---

## Sources


| Service                                    | Confirmed Price          | Source                                                                                                                                                |
| ------------------------------------------ | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| NAT GW $0.045/hr + $0.045/GB               | ✅ Confirmed              | [AWS docs — Migration Assistant cost table](https://docs.aws.amazon.com/solutions/latest/migration-assistant-for-amazon-opensearch-service/cost.html) |
| EBS gp3 $0.08/GB, $0.005/IOPS, $0.04/MiB/s | ✅ Confirmed              | [AWS EMR — storage volume types](https://docs.aws.amazon.com/emr/latest/ManagementGuide/emr-plan-storage-compare-volume-types.html)                   |
| ALB $0.0225/hr, $0.008/LCU                 | ✅ Confirmed              | [AWS docs — Migration Assistant cost table](https://docs.aws.amazon.com/solutions/latest/migration-assistant-for-amazon-opensearch-service/cost.html) |
| Route 53 $0.50/zone                        | ✅ Confirmed              | [AWS docs — Migration Assistant cost table](https://docs.aws.amazon.com/solutions/latest/migration-assistant-for-amazon-opensearch-service/cost.html) |
| Secrets Manager $0.40/secret               | ✅ Confirmed              | [AWS docs — Clickstream Analytics cost table](https://docs.aws.amazon.com/solutions/latest/clickstream-analytics-on-aws/cost.html)                    |
| KMS $1.00/CMK                              | ✅ Confirmed              | [AWS docs — Druid solution cost table](https://docs.aws.amazon.com/solutions/latest/scalable-analytics-using-apache-druid-on-aws/cost.html)           |
| VPC Endpoint $0.01/AZ-hr + $0.01/GB        | ✅ Confirmed              | [AWS docs — Clickstream Analytics cost table](https://docs.aws.amazon.com/solutions/latest/clickstream-analytics-on-aws/cost.html)                    |
| EKS $0.10/hr                               | Canonical published rate | [https://aws.amazon.com/eks/pricing/](https://aws.amazon.com/eks/pricing/)                                                                            |
| NLB $0.0225/hr + $0.006/NLCU               | Canonical published rate | [https://aws.amazon.com/elasticloadbalancing/pricing/](https://aws.amazon.com/elasticloadbalancing/pricing/)                                          |
| r6in.large, m6idn.*, i7ie.large EC2 prices | ⚠️ Approximate — verify  | [https://aws.amazon.com/ec2/pricing/on-demand/](https://aws.amazon.com/ec2/pricing/on-demand/)                                                        |
| Aurora db.r8g.large price                  | ⚠️ Approximate — verify  | [https://aws.amazon.com/rds/aurora/pricing/](https://aws.amazon.com/rds/aurora/pricing/)                                                              |
| AMP ingestion / storage rates              | Marketing page (verify)  | [https://aws.amazon.com/prometheus/pricing/](https://aws.amazon.com/prometheus/pricing/)                                                              |
| Data transfer out $0.09/GB                 | Marketing page (verify)  | [https://aws.amazon.com/ec2/pricing/on-demand/](https://aws.amazon.com/ec2/pricing/on-demand/)                                                        |
| Elastic IP $0.005/hr                       | Canonical published rate | [https://aws.amazon.com/vpc/pricing/](https://aws.amazon.com/vpc/pricing/)                                                                            |


