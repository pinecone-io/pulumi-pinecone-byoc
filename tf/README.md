# Pinecone BYOC Terraform

Deploy Pinecone in your own cloud account (AWS, GCP, or Azure) with Terraform while keeping full control over your infrastructure.

## Quick Start

The recommended first step is to run the setup wizard. It generates the `terraform.tfvars.json` file for the cloud example you choose.

### 1. Run The Setup Wizard

```bash
cd tf
python3 setup/wizard.py
```

If you rerun the wizard after changing inputs, overwrite the existing tfvars file when prompted, or pass `--force`:

```bash
python3 setup/wizard.py --force
```

This will:

1. Select your cloud provider (AWS, GCP, or Azure)
2. Check that required tools are installed (Terraform, Go, Python 3, cloud CLI, kubectl)
3. Collect Pinecone and cloud configuration
4. Generate `examples/<cloud>/terraform.tfvars.json`

### 2. Build The Local Provider

```bash
make provider-build cli-config
```

### 3. Deploy From An Example Root Module

```bash
cd examples/<cloud>
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform init
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform plan
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform apply
```

Replace `<cloud>` with `aws`, `gcp`, or `azure`. The top-level `tf/` directory is a repository root for modules, examples, setup scripts, and provider source; it is not a Terraform root module and does not contain `.tf` files to plan or apply directly.

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

Before applying the AWS example, verify the active AWS identity. The AWS module configures the AWS provider with the Terraform `region` variable, so no separate service-enable step is required:

```bash
aws sts get-caller-identity
```

**GCP**

| Tool | Purpose | Install |
|------|---------|---------|
| gcloud CLI | GCP access | [GCP docs](https://cloud.google.com/sdk/docs/install) |
| gke-gcloud-auth-plugin | GKE kubectl authentication | [GKE docs](https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl) |

Use the GCP project ID, not the project display name. Before applying the GCP example, verify access and align Application Default Credentials:

```bash
gcloud projects describe <project-id>
gcloud config set project <project-id>
gcloud auth application-default set-quota-project <project-id>
```

The GCP module enables required project services with Terraform. If Service Usage is not available yet, enable it first:

```bash
gcloud services enable serviceusage.googleapis.com --project <project-id>
```

**Azure**

| Tool | Purpose | Install |
|------|---------|---------|
| Azure CLI | Azure access | [Azure docs](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) |

Before applying the Azure example, verify subscription access:

```bash
az account show --subscription <subscription-id>
az account set --subscription <subscription-id>
```

The Azure module expects the required Azure resource providers to already be registered. If your principal cannot register providers, ask an Azure subscription owner to register them first:

```bash
for ns in Microsoft.Authorization Microsoft.Compute Microsoft.ContainerService Microsoft.DBforPostgreSQL Microsoft.KeyVault Microsoft.ManagedIdentity Microsoft.Network Microsoft.Storage; do az provider register --namespace "$ns" --subscription <subscription-id>; done
```

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

Replace `<new-version>` with the target Pinecone version, for example `main-306e425`. Version changes create a new versioned `pinetools` install job.

## Configuration

The setup wizard creates `examples/<cloud>/terraform.tfvars.json`. You can also pass values through any standard Terraform variable mechanism.

**AWS Configuration Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `pinecone_version` | Pinecone release version (required) | - |
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
| `pinecone_version` | Pinecone release version (required) | - |
| `pinecone_api_key` | Pinecone API key (required) | - |
| `project` | GCP project ID, not display name (required) | - |
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
| `pinecone_version` | Pinecone release version (required) | - |
| `pinecone_api_key` | Pinecone API key (required) | - |
| `subscription_id` | Azure subscription ID (required) | - |
| `region` | Azure region | `eastus` |
| `availability_zones` | Zones for high availability | `["1", "2"]` |
| `vpc_cidr` | VNet IP range | `10.0.0.0/16` |
| `kubernetes_version` | AKS Kubernetes version | `1.33` |
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

Build the provider and generate a Terraform CLI config for the local provider mirror:

```bash
cd tf
make provider-build cli-config
```

The `provider-build` target builds `pineconebyoc` into `tf/provider-mirror/`, and `cli-config` writes `tf/dev.tfrc.hcl` so Terraform can install that local provider during `terraform init`. Run Terraform from one of the example root modules:

```bash
cd examples/gcp
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

1. **Credentials** - Run `aws sts get-caller-identity` and confirm the expected account
2. **VPC Quota** - Request a limit increase via AWS Service Quotas
3. **Elastic IPs** - Release unused EIPs or request a limit increase
4. **NAT Gateways** - Request a limit increase
5. **EKS Clusters** - Request a limit increase if at quota

**GCP:**

1. **APIs** - Enable required APIs (compute, container, alloydb, storage, dns)
2. **Compute Quotas** - Request CPU/disk quota increases via GCP Console
3. **GKE Clusters** - Request a limit increase if at quota
4. **IP Addresses** - Release unused static IPs or request more

**Azure:**

1. **Subscription Access** - Run `az account show --subscription <subscription-id>` and confirm the expected subscription
2. **Resource Providers** - Required providers must already be registered; if registration is denied, ask a subscription owner to register them
3. **vCPU Quotas** - Request vCPU quota increases via Azure Portal
4. **AKS Clusters** - Request a limit increase if at quota
5. **Storage Accounts** - Ensure generated names are globally unique, 3-24 characters, and lowercase alphanumeric

### Deployment failures

If `terraform apply` fails partway through, fix the underlying cloud or credential issue and run `terraform apply` again. To inspect drift without making changes:

```bash
terraform plan -refresh-only
```

### GCP project not found or CONSUMER_INVALID

If Service Usage is not enabled, enable it before applying:

```bash
gcloud services enable serviceusage.googleapis.com --project <project-id>
```

### GCP Workload Identity pool not found

If GCP returns `Identity Pool does not exist (<project-id>.svc.id.goog)`, the GKE cluster's Workload Identity pool has not propagated yet. Re-run `terraform apply` from the same example directory; the module waits for that propagation before creating Workload Identity IAM bindings.

### `pinetools` install job image pull failures

If the `pinetools` install job times out with `ImagePullBackOff`, inspect the job events:

```bash
kubectl -n pc-control-plane describe job <pinetools-install-job>
kubectl -n pc-control-plane get events --sort-by=.lastTimestamp
```

The `pinecone_version` value is used as the `pinetools` image tag in the cloud-specific Pinecone registry. For GCP, that image is `us-docker.pkg.dev/pinecone-artifacts/unstable/pinetools:<pinecone_version>`. If Kubernetes reports `not found` or `403 Forbidden` for that image, use a Pinecone release tag that exists in the target registry and is available to the BYOC registry token.

### Kubernetes provider identity errors during destroy

If `terraform destroy` fails while refreshing Kubernetes Ingress resources with `Unexpected Identity Change`, delete the affected Kubernetes Ingress objects manually, remove only those stale Ingress entries from Terraform state, and rerun destroy. This can happen after a failed apply leaves incomplete Kubernetes provider identity data in state.

### Azure resource provider registration failures

If Azure returns `No registered resource provider found`, `MissingSubscriptionRegistration`, or a provider registration permission error, verify the subscription and register the required providers:

```bash
az account show --subscription <subscription-id>
for ns in Microsoft.Authorization Microsoft.Compute Microsoft.ContainerService Microsoft.DBforPostgreSQL Microsoft.KeyVault Microsoft.ManagedIdentity Microsoft.Network Microsoft.Storage; do az provider register --namespace "$ns" --subscription <subscription-id>; done
```

The Azure Terraform provider is configured with resource-provider auto-registration disabled so it does not mutate subscription-level provider registration state.

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

### Local provider mirror warnings

Terraform may warn that `pineconebyoc` is installed from an unauthenticated local mirror or that the lock file only contains checksums for your platform. That is expected while using the locally built provider through `dev.tfrc.hcl`.

If `terraform init` fails because the local `pineconebyoc` package does not match the dependency lock file checksums, remove the generated provider mirror, CLI config, and per-example initialization files, then rebuild the local provider:

```bash
cd tf
rm -rf provider-mirror dev.tfrc.hcl
find examples -mindepth 2 -maxdepth 2 \( -name .terraform -o -name .terraform.lock.hcl \) -exec rm -rf {} +
make provider-build cli-config
cd examples/<cloud>
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform init
```

This does not remove Terraform state. Only delete `terraform.tfstate` files after the old environment has been destroyed or if you intentionally want to abandon that state.

### GCP AlloyDB internal errors

GCP may occasionally return `Error code 13, message: an internal error has occurred` while creating an AlloyDB instance. If the corresponding AlloyDB cluster is `READY` and Terraform state is otherwise healthy, rerun `terraform apply`; Terraform will create the missing instance and continue with the downstream resources.

```bash
cd tf/examples/gcp
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform apply
```

### No configuration files

If `terraform init` says `Terraform initialized in an empty directory` or `terraform plan` says `No configuration files`, you are running Terraform from `tf/`. Change into an example root module first:

```bash
cd tf/examples/gcp
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform init
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform plan
```

## Cleanup

To destroy all resources:

```bash
cd tf/examples/<cloud>
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform destroy
```

For AWS and GCP, if `deletion_protection` is enabled (default), disable it first and apply the change before destroying:

```bash
cd tf/examples/<cloud>
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform apply -var='deletion_protection=false'
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform destroy
```

Azure does not expose a `deletion_protection` variable in this Terraform module; `terraform destroy` is expected to remove the successfully created Azure resources.

## Support

- [Pinecone BYOC documentation](https://docs.pinecone.io/guides/production/bring-your-own-cloud)
- Open an issue in this repository for Terraform module or provider problems
