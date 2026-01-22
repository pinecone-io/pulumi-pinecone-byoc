# Pinecone BYOC

Deploy Pinecone in your own AWS account with full control over your infrastructure.

[![Demo](https://asciinema.org/a/Aq6Hf0lzMADO5OHe.svg)]((https://asciinema.org/a/Aq6Hf0lzMADO5OHe))

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/pinecone-io/pulumi-pinecone-byoc/main/bootstrap.sh | bash
```

This will:
1. Check that required tools are installed (Python 3.12+, uv, AWS CLI, Pulumi, kubectl)
2. Verify your AWS credentials
3. Run an interactive setup wizard
4. Generate a complete Pulumi project

Then deploy:

```bash
cd pinecone-byoc
pulumi up
```

Provisioning takes approximately 25-30 minutes.

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3.12+ | Runtime | [python.org](https://www.python.org/downloads/) |
| uv | Package manager | [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |
| AWS CLI | AWS access | [AWS docs](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| Pulumi | Infrastructure | [pulumi.com/docs/install](https://www.pulumi.com/docs/install/) |
| kubectl | Cluster access | [kubernetes.io](https://kubernetes.io/docs/tasks/tools/) |

## What Gets Deployed

### Networking
- **VPC** with public and private subnets across 2 availability zones
- **NAT Gateways** for outbound internet access from private subnets
- **Network Load Balancer** for private endpoint access (PrivateLink)

### Compute
- **EKS Cluster** (Kubernetes 1.33) with managed node groups
- **Cluster Autoscaler** for automatic node scaling
- **ALB Ingress Controller** for load balancing

### Storage
- **S3 Buckets** for vector data, write-ahead logs, and operational data
- **RDS PostgreSQL** (Aurora) for control plane and system databases

### DNS & Certificates
- **Route53 Hosted Zone** for your cluster subdomain
- **ACM Certificate** with automatic DNS validation

### Platform Components
- **Gloo Edge** API gateway for routing
- **External DNS** for automatic DNS record management
- **ECR Credential Refresher** for private image registry access
- **Prometheus** for metrics collection
- **Pulumi Kubernetes Operator** for GitOps-style deployments

## Architecture

```
┌──────────────────────┐                    ┌───────────────────────────────────────────────┐
│                      │    operations      │              Your AWS Account (VPC)           │
│  Pinecone            │───────────────────▶│                                               │
│  Control Plane       │                    │  ┌─────────────┐  ┌─────────────────────────┐ │
│                      │◀───────────────────│  │  Control    │  │                         │ │
│                      │   cluster state    │  │  Plane      │  │    Cluster Manager      │ │
└──────────────────────┘                    │  └─────────────┘  │                         │ │
                                            │  ┌─────────────┐  └─────────────────────────┘ │
                                            │  │  Heartbeat  │                              │
                                            │  └─────────────┘                              │
┌──────────────────────┐                    │  ┌───────────────────────────────────────────┐│
│                      │◀───────────────────│  │                                           ││
│  Pinecone            │   metrics &        │  │              Data Plane                   ││
│  Observability (DD)  │   traces           │  │                                           ││
│                      │                    │  └───────────────────────────────────────────┘│
└──────────────────────┘                    │  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
                                            │  │    S3    │  │   RDS    │  │   Route53   │  │
        No customer data                    │  │  Buckets │  │ (Aurora) │  │   + ACM     │  │
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
- **Your data never leaves your AWS account** - only operational metrics and cluster state are transmitted
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
kubectl create job upgrade-$(date +%s) --from=cronjob/pinetools -n pc-control-plane \
  --dry-run=client -o yaml | \
  yq '.spec.template.spec.containers[0].env[0].value = "<new-version>"' | \
  kubectl create -f -
```

Replace `<new-version>` with the target Pinecone version (e.g., `main-abc1234`).

To watch the upgrade progress:

```bash
kubectl logs -f job/upgrade-<timestamp> -n pc-control-plane
```

## Configuration

The setup wizard creates a Pulumi stack with these configurable options:

| Option | Description | Default |
|--------|-------------|---------|
| `region` | AWS region | `us-east-1` |
| `availability_zones` | AZs for high availability | 2 zones |
| `vpc_cidr` | VPC IP range | `10.0.0.0/16` |
| `deletion_protection` | Protect RDS/S3 from deletion | `true` |

Edit `Pulumi.<stack>.yaml` to modify these values.

## Programmatic Usage

For advanced users who want to integrate into existing infrastructure:

```python
import pulumi
from pulumi_pinecone_byoc import PineconeAWSCluster, PineconeAWSClusterArgs

config = pulumi.Config()

cluster = PineconeAWSCluster(
    name="my-pinecone-cluster",
    args=PineconeAWSClusterArgs(
        pinecone_api_key=config.require_secret("pinecone_api_key"),
        region="us-west-2",
        availability_zones=["us-west-2a", "us-west-2b"],
        vpc_cidr="10.1.0.0/16",
        deletion_protection=True,
    ),
)

pulumi.export("cluster_endpoint", cluster.cluster_endpoint)
```

Install from PyPI:

```bash
uv add pulumi-pinecone-byoc
```

Or with pip:

```bash
pip install pulumi-pinecone-byoc
```

## Troubleshooting

### Preflight check failures

The setup wizard runs preflight checks for AWS quotas. If these fail:

1. **VPC Quota** - Request a limit increase via AWS Service Quotas
2. **Elastic IPs** - Release unused EIPs or request a limit increase
3. **NAT Gateways** - Request a limit increase
4. **EKS Clusters** - Request a limit increase

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

## License

Apache 2.0
