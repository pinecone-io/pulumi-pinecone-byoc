resource "azuread_application" "storage_integration" {
  display_name = "${local.cell_name}-storage-integration"
}

resource "azuread_service_principal" "storage_integration" {
  client_id = azuread_application.storage_integration.client_id
}

resource "azuread_service_principal_password" "storage_integration" {
  service_principal_id = azuread_service_principal.storage_integration.id
}

resource "azurerm_role_assignment" "storage_integration_reader" {
  scope              = "/subscriptions/${var.subscription_id}"
  role_definition_id = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
  principal_id       = azuread_service_principal.storage_integration.object_id
  principal_type     = "ServicePrincipal"
}
