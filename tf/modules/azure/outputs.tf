output "cluster_name" {
  value = local.cell_name
}

output "region" {
  value = var.region
}

output "organization_id" {
  value = pineconebyoc_environment.this.org_id
}

output "organization_name" {
  value = pineconebyoc_environment.this.org_name
}

output "vnet_id" {
  value = azurerm_virtual_network.this.id
}

output "kubeconfig" {
  value     = azurerm_kubernetes_cluster.this.kube_config_raw
  sensitive = true
}

output "storage_account_name" {
  value = azurerm_storage_account.this.name
}

output "control_db_endpoint" {
  value = azurerm_postgresql_flexible_server.this["control"].fqdn
}

output "system_db_endpoint" {
  value = azurerm_postgresql_flexible_server.this["system"].fqdn
}

output "environment_id" {
  value = pineconebyoc_environment.this.id
}

output "environment_name" {
  value = pineconebyoc_environment.this.env_name
}

output "service_account_id" {
  value = pineconebyoc_service_account.this.id
}

output "service_account_client_id" {
  value = pineconebyoc_service_account.this.client_id
}

output "api_key_project_id" {
  value = pineconebyoc_project_api_key.sli.project_id
}

output "subdomain" {
  value = pineconebyoc_environment.this.env_name
}

output "sli_checkers_project_id" {
  value = pineconebyoc_project_api_key.sli.project_id
}

output "cpgw_api_key" {
  value     = pineconebyoc_cpgw_api_key.this.key
  sensitive = true
}

output "cpgw_admin_api_key_id" {
  value = pineconebyoc_cpgw_api_key.this.key_id
}

output "datadog_api_key_id" {
  value = pineconebyoc_datadog_api_key.this.key_id
}

output "customer_tags" {
  value = var.tags
}

output "pulumi_backend_url" {
  value = "azblob://pulumi-state?storage_account=${azurerm_storage_account.this.name}"
}

output "pulumi_secrets_provider" {
  value = "azurekeyvault://${azurerm_key_vault.pulumi.name}.vault.azure.net/keys/pulumi-key"
}

output "private_link_service_name" {
  value = "${local.cell_name}-pls"
}

output "private_link_service_resource_group" {
  value = local.node_resource_group
}
