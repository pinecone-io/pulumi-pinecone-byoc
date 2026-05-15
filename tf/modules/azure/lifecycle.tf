resource "terraform_data" "cloud_support_ready" {
  input = {
    cluster_id = azurerm_kubernetes_cluster.this.id
    node_pool_ids = {
      for name, pool in azurerm_kubernetes_cluster_node_pool.this : name => pool.id
    }
    db_server_ids = {
      for name, server in azurerm_postgresql_flexible_server.this : name => server.id
    }
    storage_account_id = azurerm_storage_account.this.id
    key_vault_id       = azurerm_key_vault.pulumi.id
  }

  depends_on = [
    azuread_service_principal_password.storage_integration,
    azurerm_federated_identity_credential.certmanager,
    azurerm_federated_identity_credential.external_dns,
    azurerm_federated_identity_credential.prometheus,
    azurerm_federated_identity_credential.pulumi_operator,
    azurerm_key_vault_key.pulumi,
    azurerm_kubernetes_cluster_node_pool.this,
    azurerm_postgresql_flexible_server_database.this,
    azurerm_private_dns_zone_virtual_network_link.postgres,
    azurerm_role_assignment.certmanager_dns,
    azurerm_role_assignment.external_dns,
    azurerm_role_assignment.mi_operator,
    azurerm_role_assignment.network_contributor_rg,
    azurerm_role_assignment.network_contributor_subnet,
    azurerm_role_assignment.operator_keyvault,
    azurerm_role_assignment.operator_rg_contributor,
    azurerm_role_assignment.operator_storage,
    azurerm_role_assignment.storage_integration_reader,
    azurerm_storage_container.pulumi_state,
    azurerm_storage_container.this,
    azurerm_storage_management_policy.this,
    azurerm_user_assigned_identity.certmanager,
    azurerm_user_assigned_identity.cluster,
    azurerm_user_assigned_identity.external_dns,
    azurerm_user_assigned_identity.kubelet,
    azurerm_user_assigned_identity.prometheus,
    azurerm_user_assigned_identity.pulumi_operator,
    kubernetes_secret_v1.external_dns_azure,
    kubernetes_service_account_v1.external_dns,
    pineconebyoc_aks_api_server_waiter.this,
  ]
}
