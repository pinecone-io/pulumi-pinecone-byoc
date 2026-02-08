# Pinecone BYOC

[![PyPI version](https://img.shields.io/pypi/v/pulumi-pinecone-byoc)](https://pypi.org/project/pulumi-pinecone-byoc/)

Deploy Pinecone in your own cloud account (AWS or GCP) with full control over your infrastructure.

![Demo](./assets/demo.gif)

## Quick Start

### Interactive Setup

```bash
curl -fsSL https://raw.githubusercontent.com/pinecone-io/pulumi-pinecone-byoc/main/bootstrap.sh | bash
```

This will:
1. Select your cloud provider (AWS or GCP)
2. Check that required tools are installed (Python 3.12+, uv, cloud CLI, Pulumi, kubectl)
3. Verify your cloud credentials
4. Run an interactive setup wizard
5. Generate a complete Pulumi project

Then deploy:

```bash
cd pinecone-byoc
pulumi up
```

Provisioning takes approximately 25-30 minutes.

## Prerequisites

### Common Tools (Required for Both AWS and GCP)

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3.12+ | Runtime | [python.org](https://www.python.org/downloads/) |
| uv | Package manager | [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |
| Pulumi | Infrastructure | [pulumi.com/docs/install](https://www.pulumi.com/docs/install/) |
| kubectl | Cluster access | [kubernetes.io](https://kubernetes.io/docs/tasks/tools/) |

### Cloud-Specific Tools

**AWS**
| Tool | Purpose | Install |
|------|---------|---------|
| AWS CLI | AWS access | [AWS docs](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |

**GCP**
| Tool | Purpose | Install |
|------|---------|---------|
| gcloud CLI | GCP access | [GCP docs](https://cloud.google.com/sdk/docs/install) |

## Architecture

```
┌──────────────────────┐                    ┌───────────────────────────────────────────────┐
│                      │    operations      │              Your AWS/GCP Account (VPC)       │
│  Pinecone            │───────────────────▶│                                               │
│  Control Plane       │                    │  ┌─────────────┐  ┌─────────────────────────┐ │
│                      │◀───────────────────│  │  Control    │  │                         │ │
│                      │   cluster state    │  │  Plane      │  │    Cluster Manager      │ │
└──────────────────────┘                    │  └─────────────┘  │        (EKS/GKE)        │ │
                                            │  ┌─────────────┐  └─────────────────────────┘ │
                                            │  │  Heartbeat  │                              │
                                            │  └─────────────┘                              │
┌──────────────────────┐                    │  ┌───────────────────────────────────────────┐│
│                      │◀───────────────────│  │                                           ││
│  Pinecone            │   metrics &        │  │              Data Plane                   ││
│  Observability (DD)  │   traces           │  │                                           ││
│                      │                    │  └───────────────────────────────────────────┘│
└──────────────────────┘                    │  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
                                            │  │  S3/GCS  │  |   RDS/   |  │  Route53/   │  │
        No customer data                    │  │  Buckets │  │  Alloy   │  |  Cloud DNS  |  │
        leaves the cluster                  │  └──────────┘  └──────────┘  └─────────────┘  │
                                            └───────────────────────────────────────────────┘
```

## How It Works

Pinecone BYOC uses a **pull-based model** for control plane operations:

1. **Index Operations** - When you create, scale, or delete indexes through the Pinecone API, these operations are queued in Pinecone's control plane
2. **Pull & Execute** - Components running in your cluster continuously pull pending operations and execute them locally
3. **Heartbeat & State** - Your cluster pushes health status and state back to Pinecone for monitoring
4. **Observability** - Metrics and traces (not customer data) are sent to Pinecone's observability platform (Datadog) for operational insights

This architecture ensures:
- **Your data never leaves your cloud account** - only operational metrics and cluster state are transmitted
- Network security policies remain under your control
- All communication is outbound from your cluster - Pinecone never needs inbound access

## Cluster Access

After deployment, configure kubectl:

```bash
aws eks update-kubeconfig --region <region> --name <cluster-name>
```

The exact command is output after `pulumi up` completes.

## Upgrades

Pinecone manages upgrades automatically in the background. If you need to trigger an upgrade manually:

```bash
pulumi up -c pinecone-version=<new-version>
```

Replace `<new-version>` with the target Pinecone version (e.g., `main-abc1234`).

## Configuration

The setup wizard creates a Pulumi stack with these configurable options:

**AWS Configuration Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `pinecone-version` | Pinecone release version (required) | — |
| `region` | AWS region | `us-east-1` |
| `availability_zones` | AZs for high availability | `["us-east-1a", "us-east-1b"]` |
| `vpc_cidr` | VPC IP range | `10.0.0.0/16` |
| `deletion_protection` | Protect RDS/S3 from accidental deletion | `true` |
| `public_access_enabled` | Enable public endpoint (false = PrivateLink only) | `true` |
| `tags` | Custom tags to apply to all resources | `{}` |

**GCP Configuration Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `pinecone-version` | Pinecone release version (required) | — |
| `gcp_project` | GCP project ID (required) | — |
| `region` | GCP region | `us-central1` |
| `availability_zones` | Zones for high availability | `["us-central1-a", "us-central1-b"]` |
| `vpc_cidr` | VPC IP range | `10.112.0.0/12` |
| `deletion_protection` | Protect AlloyDB/GCS from accidental deletion | `true` |
| `public_access_enabled` | Enable public endpoint (false = Private Service Connect only) | `true` |
| `labels` | Custom labels to apply to all resources | `{}` |

Edit `Pulumi.<stack>.yaml` to modify these values.

## Programmatic Usage

For advanced users who want to integrate into existing infrastructure:

```python
import pulumi
from pulumi_pinecone_byoc.aws import PineconeAWSCluster, PineconeAWSClusterArgs

config = pulumi.Config()

cluster = PineconeAWSCluster(
    "pinecone-aws-cluster",
    PineconeAWSClusterArgs(
        pinecone_api_key=config.require_secret("pinecone_api_key"),
        pinecone_version=config.require("pinecone_version"),
        region=config.require("region"),
        availability_zones=config.require_object("availability_zones"),
        vpc_cidr=config.get("vpc_cidr") or "10.0.0.0/16",
        deletion_protection=config.get_bool("deletion_protection") if config.get_bool("deletion_protection") is not None else True,
        public_access_enabled=config.get_bool("public_access_enabled") if config.get_bool("public_access_enabled") is not None else True,
        tags=config.get_object("tags") or {},
    ),
)

# Export useful values
pulumi.export("environment", cluster.environment.env_name)
pulumi.export("cluster_name", cluster.cell_name)
pulumi.export("kubeconfig", cluster.eks.kubeconfig)
```

# Export useful values
pulumi.export("environment", cluster.environment.env_name)
pulumi.export("cluster_name", cluster.cell_name)
pulumi.export("kubeconfig", cluster.gke.kubeconfig)
```

### Installation

Install from PyPI with cloud-specific dependencies:

```bash
# For AWS
uv add 'pulumi-pinecone-byoc[aws]'

# For GCP
uv add 'pulumi-pinecone-byoc[gcp]'
```

## Troubleshooting

### Preflight check failures

The setup wizard runs preflight checks for cloud quotas. If these fail:

**AWS:**
1. **VPC Quota** - Request a limit increase via AWS Service Quotas
2. **Elastic IPs** - Release unused EIPs or request a limit increase
3. **NAT Gateways** - Request a limit increase
4. **EKS Clusters** - Request a limit increase

**GCP:**
1. **APIs** - Enable required APIs (compute, container, alloydb, storage, dns)
2. **Compute Quotas** - Request CPU/disk quota increases via GCP Console
3. **GKE Clusters** - Request a limit increase if at quota
4. **IP Addresses** - Release unused static IPs or request more

### Deployment failures

If `pulumi up` fails partway through:

```bash
pulumi refresh  # Sync state with actual resources
pulumi up       # Retry deployment
```

### Cluster access issues

Ensure your AWS credentials match the account where the cluster is deployed:

```bash
aws sts get-caller-identity
```

## Cleanup

To destroy all resources:

```bash
pulumi destroy
```

Note: If `deletion_protection` is enabled (default), you'll need to disable it first or manually delete protected resources.

## Support

- [Documentation](https://docs.pinecone.io/guides/production/bring-your-own-cloud)
- [GitHub Issues](https://github.com/pinecone-io/pulumi-pinecone-byoc/issues)
