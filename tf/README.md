# Pinecone BYOC Terraform

Deploy Pinecone in your own cloud account (AWS, GCP, or Azure) with Terraform while keeping full control over your infrastructure.

## Quick Start

### Interactive Setup

```bash
cd tf
python3 setup/wizard.py
```

This will:

1. Select your cloud provider (AWS, GCP, or Azure)
2. Check that required tools are installed (Terraform, Go, Python 3, cloud CLI, kubectl)
3. Collect Pinecone and cloud configuration
4. Generate `examples/<cloud>/terraform.tfvars.json`

Then build the local Pinecone BYOC provider and deploy:

```bash
make provider-build cli-config
cd examples/<cloud>
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform init
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform apply
```

Provisioning takes approximately 25-30 minutes.

## Prerequisites

### Common Tools (Required for All Clouds)

| Tool | Purpose | Install |
|------|---------|---------|
| Terraform 1.6+ | Infrastructure | [terraform.io](https://developer.hashicorp.com/terraform/install) |
| Go | Build the local Pinecone BYOC provider | [go.dev](https://go.dev/doc/install) |
| Python 3 | Setup wizard and preflight helpers | [python.org](https://www.python.org/downloads/) |
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
| gke-gcloud-auth-plugin | GKE kubectl authentication | [GKE docs](https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl) |

**Azure**

| Tool | Purpose | Install |
|------|---------|---------|
| Azure CLI | Azure access | [Azure docs](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) |

## Architecture

```
+----------------------+                    +-----------------------------------------------+
|                      |    operations      |         Your AWS/GCP/Azure Account (VPC)      |
|  Pinecone            |------------------->|                                               |
|  Control Plane       |                    |  +-------------+  +-------------------------+ |
|                      |<-------------------|  |  Control    |  |                         | |
|                      |   cluster state    |  |  Plane      |  |    Cluster Manager      | |
+----------------------+                    |  +-------------+  |     (EKS/GKE/AKS)       | |
                                            |  +-------------+  +-------------------------+ |
                                            |  |  Heartbeat  |                              |
                                            |  +-------------+                              |
+----------------------+                    |  +-------------------------------------------+|
|                      |<-------------------|  |                                           ||
|  Pinecone            |   metrics &        |  |              Data Plane                   ||
|  Observability (DD)  |   traces           |  |                                           ||
|                      |                    |  +-------------------------------------------+|
+----------------------+                    |  +----------+  +-----------+  +-------------+ |
                                            |  | S3/GCS/  |  |RDS/AlloyDB|  | Route53/    | |
        No customer data                    |  | AzureBlob|  |/AzurePGSQL|  | CloudDNS/   | |
        leaves the cluster                  |  +----------+  +-----------+  | Azure DNS   | |
                                            |                               +-------------+ |
                                            +-----------------------------------------------+
```

## How It Works

Pinecone BYOC uses a **pull-based model** for control plane operations:

1. **Index Operations** - When you create, scale, or delete indexes through the Pinecone API, these operations are queued in Pinecone's control plane
2. **Pull & Execute** - Components running in your cluster continuously pull pending operations and execute them locally
3. **Heartbeat & State** - Your cluster pushes health status and state back to Pinecone for monitoring
4. **Observability** - Metrics and traces (not customer data) are sent to Pinecone's observability platform (Datadog) for operational insights

Terraform provisions the cloud network, Kubernetes cluster, storage, databases, DNS, private connectivity, Kubernetes configuration, registry refresh job, and versioned `pinetools` install job. Pinecone BYOC control-plane lifecycle calls and destroy-time cluster uninstall behavior are handled by the `pineconebyoc` Terraform provider.

This architecture ensures:

- **Your data never leaves your cloud account** - only operational metrics and cluster state are transmitted
- Network security policies remain under your control
- All communication is outbound from your cluster - Pinecone never needs inbound access

## Cluster Access

After deployment, use the sensitive `kubeconfig` output or your cloud CLI.

```bash
terraform output -raw kubeconfig > kubeconfig
KUBECONFIG=./kubeconfig kubectl get namespaces
```

**AWS:**

```bash
terraform output -raw update_kubeconfig_command
aws eks update-kubeconfig --region <region> --name <cluster-name>
```

**GCP:**

```bash
gcloud container clusters get-credentials <cluster-name> --region <region> --project <project-id>
```

**Azure:**

```bash
az aks get-credentials --resource-group <resource-group> --name <cluster-name>
```

The exact cluster name, region, kubeconfig, and cloud-specific connectivity outputs are available from `terraform output`.

## Upgrades

Update the `pinecone_version` variable and run Terraform again:

```bash
terraform apply -var='pinecone_version=<new-version>'
```

Replace `<new-version>` with the target Pinecone version, for example `main-abc1234`. Version changes create a new versioned `pinetools` install job.

## Configuration

The setup wizard creates `examples/<cloud>/terraform.tfvars.json`. You can also pass values through any standard Terraform variable mechanism.

**AWS Configuration Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `pinecone_version` | Pinecone release version, used as the `pinetools` container image tag (required, e.g. `main-1b955e2`) | - |
| `pinecone_api_key` | Pinecone API key (required) | - |
| `region` | AWS region | `us-east-1` |
| `availability_zones` | AZs for high availability | `["us-east-1a", "us-east-1b"]` |
| `vpc_cidr` | VPC IP range | `10.0.0.0/16` |
| `kubernetes_version` | EKS Kubernetes version | `1.33` |
| `deletion_protection` | Protect RDS/S3 from accidental deletion | `true` |
| `public_access_enabled` | Enable public endpoint (false = PrivateLink only) | `true` |
| `custom_ami_id` | Optional custom node AMI ID | `null` |
| `kms_key_arn` | Optional existing KMS key ARN | `null` |
| `tags` | Custom tags to apply to resources | `{}` |

**GCP Configuration Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `pinecone_version` | Pinecone release version, used as the `pinetools` container image tag (required, e.g. `main-1b955e2`) | - |
| `pinecone_api_key` | Pinecone API key (required) | - |
| `project` | GCP project ID (required) | - |
| `region` | GCP region | `us-central1` |
| `availability_zones` | Zones for high availability | `["us-central1-a", "us-central1-b"]` |
| `vpc_cidr` | VPC IP range | `10.112.0.0/12` |
| `kubernetes_version` | GKE Kubernetes version | `1.33` |
| `deletion_protection` | Protect AlloyDB/GCS from accidental deletion | `true` |
| `public_access_enabled` | Enable public endpoint (false = Private Service Connect only) | `true` |
| `labels` | Custom labels to apply to resources | `{}` |

**Azure Configuration Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `pinecone_version` | Pinecone release version, used as the `pinetools` container image tag (required, e.g. `main-1b955e2`) | - |
| `pinecone_api_key` | Pinecone API key (required) | - |
| `subscription_id` | Azure subscription ID (required) | - |
| `region` | Azure region | `eastus` |
| `availability_zones` | Zones for high availability | `["1", "2"]` |
| `vpc_cidr` | VNet IP range | `10.0.0.0/16` |
| `kubernetes_version` | AKS Kubernetes version | `1.33` |
| `deletion_protection` | Protect databases/storage from accidental deletion | `true` |
| `public_access_enabled` | Enable public endpoint (false = Private Link only) | `true` |
| `tags` | Custom tags to apply to resources | `{}` |

## Module Usage

For advanced users who want to integrate into an existing Terraform root module:

```hcl
module "pinecone" {
  source = "./modules/gcp"

  pinecone_api_key   = var.pinecone_api_key
  pinecone_version   = var.pinecone_version
  project            = var.project
  region             = "us-central1"
  availability_zones = ["us-central1-a", "us-central1-b"]
}

output "environment_name" {
  value = module.pinecone.environment_name
}

output "cluster_name" {
  value = module.pinecone.cluster_name
}
```

## Installation

Build the provider and generate a Terraform CLI development override:

```bash
cd tf
make provider-build cli-config
```

The generated `dev.tfrc.hcl` tells Terraform to use the local `pineconebyoc` provider binary. Use it when running Terraform commands:

```bash
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform init
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform plan
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform apply
```

Useful repository paths:

- `modules/aws`, `modules/gcp`, `modules/azure`: Cloud-specific Pinecone BYOC modules
- `modules/common`: Shared Kubernetes secrets, config maps, registry credential refresher, versioned install job, and lifecycle wiring
- `provider/pineconebyoc`: Terraform provider for Pinecone BYOC lifecycle resources and waiters
- `examples/aws`, `examples/gcp`, `examples/azure`: Runnable example root modules
- `setup/wizard.py`: Interactive setup wizard

## Troubleshooting

### Preflight check failures

The setup wizard runs preflight checks for cloud quotas and required APIs. If these fail:

**AWS:**

1. **VPC Quota** - Request a limit increase via AWS Service Quotas
2. **Elastic IPs** - Release unused EIPs or request a limit increase
3. **NAT Gateways** - Request a limit increase
4. **EKS Clusters** - Request a limit increase if at quota

**GCP:**

1. **APIs** - Enable required APIs (compute, container, alloydb, storage, dns)
2. **Compute Quotas** - Request CPU/disk quota increases via GCP Console
3. **GKE Clusters** - Request a limit increase if at quota
4. **IP Addresses** - Release unused static IPs or request more

**Azure:**

1. **Resource Providers** - Register required providers (Microsoft.Compute, Microsoft.ContainerService, Microsoft.Network, Microsoft.DBforPostgreSQL, Microsoft.Storage, Microsoft.KeyVault)
2. **vCPU Quotas** - Request vCPU quota increases via Azure Portal
3. **AKS Clusters** - Request a limit increase if at quota
4. **Storage Accounts** - Ensure generated names are globally unique, 3-24 characters, and lowercase alphanumeric

### Deployment failures

If `terraform apply` fails partway through, fix the underlying cloud or credential issue and run `terraform apply` again. To inspect drift without making changes:

```bash
terraform plan -refresh-only
```

### Cluster access issues

Ensure your cloud credentials match the account where the cluster is deployed:

```bash
# AWS
aws sts get-caller-identity

# GCP
gcloud auth list
gcloud config get-value project

# Azure
az account show
```

### Provider override warnings

Terraform may warn that provider development overrides are active. That is expected when using the locally built `pineconebyoc` provider through `dev.tfrc.hcl`.

## Cleanup

To destroy all resources:

```bash
terraform destroy
```

If `deletion_protection` is enabled (default), disable it first and apply the change before destroying:

```bash
terraform apply -var='deletion_protection=false'
terraform destroy
```

## Support

- [Pinecone BYOC documentation](https://docs.pinecone.io/guides/production/bring-your-own-cloud)
- Open an issue in this repository for Terraform module or provider problems
