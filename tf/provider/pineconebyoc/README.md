# Pinecone BYOC Terraform Provider

`pineconebyoc` is the Terraform provider used by the Pinecone BYOC modules for Pinecone control-plane lifecycle resources and deployment waiters.

Most infrastructure is managed by standard Terraform providers such as `aws`, `google`, `google-beta`, `azurerm`, `azuread`, `kubernetes`, `helm`, `random`, and `tls`. This provider is intentionally limited to Pinecone BYOC resources and imperative lifecycle steps that are specific to the BYOC install flow.

## Resources

- `pineconebyoc_environment`
- `pineconebyoc_cpgw_api_key`
- `pineconebyoc_service_account`
- `pineconebyoc_project_api_key`
- `pineconebyoc_dns_delegation`
- `pineconebyoc_datadog_api_key`
- `pineconebyoc_amp_access`
- `pineconebyoc_cluster_uninstaller`
- `pineconebyoc_kubernetes_retained_namespace`
- `pineconebyoc_aws_alb_waiter`
- `pineconebyoc_aws_vpc_endpoint_dns_verification`
- `pineconebyoc_gcp_forwarding_rule_waiter`
- `pineconebyoc_gcp_forwarding_rule_delete_waiter`
- `pineconebyoc_aks_api_server_waiter`

## Build

From the Terraform repository root:

```sh
make provider-build cli-config
```

Or build the provider directly:

```sh
cd provider/pineconebyoc
go build -o bin/terraform-provider-pineconebyoc
```

## Terraform Local Mirror

Until the provider is published to a registry, Terraform installs it from a local filesystem mirror:

```hcl
provider_installation {
  filesystem_mirror {
    path    = "/absolute/path/to/tf/provider-mirror"
    include = ["pinecone.io/internal/pineconebyoc"]
  }
  direct {
    exclude = ["pinecone.io/internal/pineconebyoc"]
  }
}
```

The `make provider-build` target builds the provider into `tf/provider-mirror/`, and `make cli-config` writes this configuration to `tf/dev.tfrc.hcl`. Use it by setting `TF_CLI_CONFIG_FILE` when running Terraform:

```sh
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform init
TF_CLI_CONFIG_FILE=../../dev.tfrc.hcl terraform apply
```

## Tests

Run the provider unit tests:

```sh
cd provider/pineconebyoc
go test ./...
```
