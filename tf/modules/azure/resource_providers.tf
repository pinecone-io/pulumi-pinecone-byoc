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

resource "azurerm_resource_provider_registration" "required" {
  for_each = local.required_resource_providers

  name = each.value
}

resource "terraform_data" "azure_resource_providers_ready" {
  input = {
    providers = sort([for provider in azurerm_resource_provider_registration.required : provider.name])
  }

  depends_on = [azurerm_resource_provider_registration.required]
}
