resource "azurerm_storage_container" "pulumi_state" {
  name                  = "pulumi-state"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_key_vault" "pulumi" {
  name                       = local.key_vault_name
  resource_group_name        = azurerm_resource_group.this.name
  location                   = var.region
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  tags                       = local.tags
}

resource "azurerm_key_vault_key" "pulumi" {
  name         = "pulumi-key"
  key_vault_id = azurerm_key_vault.pulumi.id
  key_type     = "RSA"
  key_size     = 2048
  key_opts     = ["decrypt", "encrypt", "sign", "unwrapKey", "verify", "wrapKey"]
}

resource "azurerm_user_assigned_identity" "pulumi_operator" {
  name                = "pulumi-operator-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "pulumi_operator" {
  name                = "pulumi-operator"
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.pulumi_operator.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:pulumi-kubernetes-operator:pulumi-k8s-operator"
}

resource "azurerm_role_assignment" "operator_storage" {
  scope                = azurerm_storage_container.pulumi_state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.pulumi_operator.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "operator_keyvault" {
  scope                = azurerm_key_vault.pulumi.id
  role_definition_name = "Key Vault Crypto User"
  principal_id         = azurerm_user_assigned_identity.pulumi_operator.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "operator_rg_contributor" {
  scope                = azurerm_resource_group.this.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.pulumi_operator.principal_id
  principal_type       = "ServicePrincipal"
}
