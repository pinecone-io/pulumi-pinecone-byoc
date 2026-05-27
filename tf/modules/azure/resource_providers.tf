locals {
  required_resource_providers = toset([
    "Microsoft.Authorization",
    "Microsoft.Compute",
    "Microsoft.ContainerService",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.KeyVault",
    "Microsoft.ManagedIdentity",
    "Microsoft.Network",
    "Microsoft.Storage",
  ])
}

resource "terraform_data" "azure_resource_providers_ready" {
  input = {
    providers = sort(tolist(local.required_resource_providers))
  }
}
