# AWS Infrastructure Breakdown

This document describes all AWS resources provisioned by the `pulumi-pinecone-byoc` codebase for a Pinecone BYOC deployment.

---

## 1. Networking (VPC)

**Source:** `pulumi_pinecone_byoc/aws/vpc.py`

- **VPC** — /16 CIDR (default: `10.0.0.0/16`), RFC 1918 enforced
- **Subnets per AZ** (up to 3 AZs):
  - Public /20 — tagged `kubernetes.io/role/elb=1`, map public IP on launch
  - Private /18 — tagged `kubernetes.io/role/internal-elb=1`, hosts EKS nodes and RDS
- **Internet Gateway** — public subnet outbound access
- **NAT Gateways** — one per AZ, each with an Elastic IP, private subnet internet access
- **Route Tables** — public routes to IGW; private routes to AZ-local NAT GW

---

## 2. EKS Cluster

**Source:** `pulumi_pinecone_byoc/aws/eks.py`

- **Managed EKS cluster** — Kubernetes 1.33 (default, configurable)
- **Control plane logging** — API, audit, authenticator, controllerManager, scheduler
- **OIDC Provider** — enables IRSA (IAM Roles for Service Accounts)
- **API auth mode** — API (not token-based)
- **Endpoint access** — private and public

### Node Groups

- Configurable via `node_pools` list; defaults to a single `default` pool
- Default instance type: `r6in.large`
- Min: 1 / Max: 10 / Desired: 3 (configurable)
- 100 GB gp3 EBS volumes (delete on termination)
- IMDSv1/v2 support (hop limit 2 for pod-level access)
- Optional custom AMI with nodeadm bootstrap

### Required Instance Types (wizard preflight check)

The setup wizard verifies availability of all four instance types in the selected AZs:

| Instance Type | Role |
|---------------|------|
| `r6in.large` | Default / general workloads |
| `m6idn.large` | Memory-optimized workloads |
| `m6idn.xlarge` | Memory-optimized (larger) workloads |
| `i7ie.large` | Storage-optimized workloads |

> **Note:** The codebase checks for `i7ie.large` — **not** `i7i.large`. These are different instance families. If you are planning to deploy a b1-tier instance, confirm with Pinecone that this maps to `i7ie.large` and not `i7i.large`.

### IAM Roles

- **Cluster Role** — `AmazonEKSClusterPolicy`, `AmazonEKSVPCResourceController`
- **Node Role** — `AmazonEKSWorkerNodePolicy`, `AmazonEKS_CNI_Policy`, `AmazonEC2ContainerRegistryReadOnly`, `AmazonSSMManagedInstanceCore`, `AmazonS3FullAccess`, `AmazonRoute53FullAccess`

---

## 3. S3 Buckets (5 total)

**Source:** `pulumi_pinecone_byoc/aws/s3.py`

All buckets share: versioning enabled, all public access blocked, AES256 (or KMS) encryption, and lifecycle rules.

| Bucket Name | Purpose |
|-------------|---------|
| `pc-data-{cell}` | Vector data storage |
| `pc-index-backups-{cell}` | Index snapshots |
| `pc-wal-{cell}` | Write-ahead logs |
| `pc-janitor-{cell}` | Cleanup operations |
| `pc-internal-{cell}` | Operational data |

**Lifecycle rules (all buckets):**
- Abort incomplete multipart uploads after 2 days
- Delete expired object delete markers
- Noncurrent versions expire after 3 days
- Activity scrapes deleted after 30 days
- Janitor files deleted after 7 days
- Lag reporter files deleted after 14 days

---

## 4. RDS Aurora PostgreSQL (2 clusters)

**Source:** `pulumi_pinecone_byoc/aws/rds.py`

| Cluster | Database | Username | Instance Class |
|---------|----------|----------|----------------|
| `control-db` | `controller` | `controller` | `db.r8g.large` |
| `system-db` | `systemdb` | `systemuser` | `db.r8g.large` |

**Shared configuration:**
- Engine: Aurora PostgreSQL 15.15 (configurable)
- Private subnets only, not publicly accessible
- Encryption: AES256 or KMS
- Backup retention: 7 days (window: 03:00–04:00 UTC)
- Maintenance window: Sunday 04:00–05:00 UTC
- Performance Insights: enabled (7-day retention)
- Deletion protection: enabled by default
- Master passwords and connection info stored in **AWS Secrets Manager**
- Security group: ingress port 5432 from VPC CIDR only

---

## 5. Load Balancers

**Source:** `pulumi_pinecone_byoc/aws/nlb.py`

| Resource | Type | Scheme | Purpose |
|----------|------|--------|---------|
| NLB | Network (L4) | Internal | TCP 443 ingress, targets private ALBs |
| Private ALB (gRPC) | Application (L7) | Internal | HTTP/2 traffic to pods |
| Private ALB (REST) | Application (L7) | Internal | HTTP/1 traffic to pods |
| Public ALB (gRPC) | Application (L7) | Internet-facing | Optional, when `public_access_enabled=true` |
| Public ALB (REST) | Application (L7) | Internet-facing | Optional, when `public_access_enabled=true` |

**Security groups:**
- NLB: ingress TCP 443 from `0.0.0.0/0`; egress TCP 443 to `0.0.0.0/0`
- Private ALB: ingress TCP 443 from NLB SG only; egress all

---

## 6. DNS & Certificates

**Source:** `pulumi_pinecone_byoc/aws/dns.py`

- **Route 53 hosted zone** — `{subdomain}.byoc.pinecone.io`
- **ACM certificate (public)** — `*.{fqdn}` with SANs for `{fqdn}` and `*.svc.{fqdn}`
- **ACM certificate (private)** — for PrivateLink endpoint access
- **DNS records** — CNAMEs to ingress ALB, validation TXT records, ALIAS for `ingress.{fqdn}`

---

## 7. PrivateLink

- **VPC Endpoint Service** — backed by NLB, accepts all principals, private DNS `*.private.{subdomain}.byoc.pinecone.io`
- **VPC Interface Endpoint** — in private subnets, private DNS enabled (15-minute timeout)

---

## 8. IAM Roles (IRSA — Kubernetes Add-ons)

**Source:** `pulumi_pinecone_byoc/aws/k8s_addons.py`

| Role | Service Account | Key Permissions |
|------|----------------|-----------------|
| ALB Controller | `kube-system:aws-lb-controller-sa` | EC2/ELB/ACM/WAF (220+ permissions) |
| Cluster Autoscaler | `kube-system:cluster-autoscaler-sa` | Describe/modify ASGs, EC2 instance types, EKS node groups |
| External DNS | `gloo-system:external-dns` | Route 53 record changes, list zones |
| EBS CSI Driver | `kube-system:ebs-csi-controller-sa` | `AmazonEBSCSIDriverPolicy` (AWS managed) |
| AZ Rebalance | `pc-control-plane:suspend-azrebalance-sa` | Describe/suspend ASG processes |
| AMP Ingest | `prometheus:amp-iamproxy-ingest-service-account` | Remote write to Amazon Managed Prometheus |

---

## 9. Pulumi Operator State Management

**Source:** `pulumi_pinecone_byoc/aws/pulumi_operator.py`

- **S3 state bucket** (`pc-pulumi-state-{cell}`) — versioned, AES256, noncurrent versions expire after 30 days
- **KMS key** — for Pulumi secrets encryption, key rotation enabled; alias: `alias/{prefix}-pulumi-secrets-{suffix}`
- **Pulumi Operator IAM role** — IRSA for `pulumi-kubernetes-operator:*`; permissions: S3 state bucket read/write, KMS encrypt/decrypt, EKS node group and launch template management

---

## 10. Cross-Account Storage Integration

**Source:** `pulumi_pinecone_byoc/aws/cluster.py`

- **Storage Integration Role** — allows current account root to assume; inline policy: `s3:ListBucket`, `s3:GetObject` on all resources
- **Node Role inline policy** — `sts:AssumeRole` on all roles, enabling cross-account access for data import

---

## Resource Naming Conventions

- **Pattern:** `{resource_prefix}-{component}-{suffix}`
- **Resource prefix:** derived from the environment/project name
- **Suffix:** last 4 characters of the cell name
- **S3 buckets:** `pc-{type}-{cell_name}`

---

## Architecture Overview

```
Internet ──→ Public ALBs (optional) ──→ NLB (TCP 443, internal) ──→ Private ALBs ──→ EKS Pods
                                                                              |
                                              ┌───────────────────────────────────────────┐
                                              │              EKS Cluster                  │
                                              │   (private subnets, gp3 EBS volumes)      │
                                              └────────────┬──────────┬───────────────────┘
                                                           ↓          ↓
                                                    S3 (5 buckets)   Aurora PG (2 clusters)
                                                           ↓
                                              Route 53 / ACM / Secrets Manager / KMS
```

---

## Instance Type Validation Notes

### `m6idn.xlarge`
The codebase lists `m6idn.xlarge` as one of four instance types verified by the setup wizard's preflight check (`setup/wizard.py:693`). **There is no hardcoded quantity of 2** in the library code — node pool sizes are driven by your deployment configuration. If you are deploying 2 `m6idn.xlarge` nodes, that count lives in your Pulumi stack config, not in this library.

### b1 instance (`i7i.large` vs `i7ie.large`)
The instance type referenced in the codebase for storage-optimized workloads is **`i7ie.large`** — not `i7i.large`. These are different AWS instance families:

| Family | Full Name | Notes |
|--------|-----------|-------|
| `i7i` | 7th-gen storage optimized | Standard NVMe SSD |
| `i7ie` | 7th-gen storage optimized (enhanced) | Higher I/O, enhanced networking |

**Recommendation:** If you plan to launch a b1-tier instance, verify with Pinecone that it maps to `i7ie.large` (as checked by this codebase) rather than `i7i.large`. Using the wrong instance family could result in a preflight check failure or capacity mismatch.
