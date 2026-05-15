locals {
  azure_pulumi_outputs = {
    cell_name                           = local.cell_name
    org_name                            = pineconebyoc_environment.this.org_name
    cloud                               = "azure"
    region                              = var.region
    global_env                          = var.global_env
    subdomain                           = pineconebyoc_environment.this.env_name
    availability_zones                  = var.availability_zones
    dns_zone_name                       = azurerm_dns_zone.this.name
    azure_k8s_version                   = var.kubernetes_version
    azure_subscription_id               = var.subscription_id
    azure_subnet_id                     = azurerm_subnet.aks.id
    azure_client_id                     = azurerm_user_assigned_identity.external_dns.client_id
    azure_certmanager_client_id         = azurerm_user_assigned_identity.certmanager.client_id
    azure_tenant_id                     = data.azurerm_client_config.current.tenant_id
    azure_resource_group                = azurerm_resource_group.this.name
    azure_pulumi_operator_client_id     = azurerm_user_assigned_identity.pulumi_operator.client_id
    data_storage_account_name           = azurerm_storage_account.this.name
    image_registry                      = local.registry_base
    sli_checkers_project_id             = pineconebyoc_project_api_key.sli.project_id
    gcp_project                         = var.gcp_project
    cpgw_admin_api_key_id               = pineconebyoc_cpgw_api_key.this.key_id
    api_url                             = var.api_url
    auth0_domain                        = var.auth0_domain
    customer_tags                       = var.tags
    public_access_enabled               = var.public_access_enabled
    pulumi_backend_url                  = "azblob://pulumi-state?storage_account=${azurerm_storage_account.this.name}"
    pulumi_secrets_provider             = "azurekeyvault://${azurerm_key_vault.pulumi.name}.vault.azure.net/keys/pulumi-key"
    aws_amp_region                      = pineconebyoc_amp_access.this.amp_region
    aws_amp_remote_write_url            = pineconebyoc_amp_access.this.amp_remote_write_endpoint
    aws_amp_sigv4_role_arn              = pineconebyoc_amp_access.this.pinecone_role_arn
    aws_amp_ingest_role_arn             = ""
    azure_storage_integration_tenant_id = data.azurerm_client_config.current.tenant_id
    azure_storage_integration_client_id = azuread_application.storage_integration.client_id
  }
}

module "common" {
  source = "../common"

  cloud                    = "azure"
  cell_name                = local.cell_name
  environment              = var.global_env
  is_prod                  = var.global_env == "prod"
  domain                   = pineconebyoc_environment.this.env_name
  region                   = var.region
  public_access_enabled    = var.public_access_enabled
  api_url                  = var.api_url
  registry_type            = "acr"
  pinetools_image          = local.pinetools_image
  pinecone_version         = var.pinecone_version
  cpgw_api_key             = pineconebyoc_cpgw_api_key.this.key
  gcps_api_key             = pineconebyoc_project_api_key.sli.value
  datadog_api_key          = pineconebyoc_datadog_api_key.this.api_key
  azure_storage_access_key = azurerm_storage_account.this.primary_access_key

  storage_integration_credentials = {
    client-secret = azuread_service_principal_password.storage_integration.value
  }

  db_credentials = {
    control = {
      host          = azurerm_postgresql_flexible_server.this["control"].fqdn
      readonly_host = azurerm_postgresql_flexible_server.this["control"].fqdn
      port          = "5432"
      username      = local.dbs.control.username
      password      = random_password.db["control"].result
      dbname        = local.dbs.control.db_name
    }
    system = {
      host          = azurerm_postgresql_flexible_server.this["system"].fqdn
      readonly_host = azurerm_postgresql_flexible_server.this["system"].fqdn
      port          = "5432"
      username      = local.dbs.system.username
      password      = random_password.db["system"].result
      dbname        = local.dbs.system.db_name
    }
  }

  pulumi_outputs = local.azure_pulumi_outputs

  depends_on = [
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
    azurerm_kubernetes_cluster_node_pool.this,
    kubernetes_service_v1.internal_lb,
    azurerm_dns_a_record.private,
    azurerm_storage_management_policy.this,
    azurerm_postgresql_flexible_server_database.this,
    azurerm_role_assignment.operator_storage,
    azurerm_role_assignment.operator_keyvault,
    azurerm_role_assignment.storage_integration_reader,
  ]
}

resource "pineconebyoc_cluster_uninstaller" "this" {
  kubeconfig      = azurerm_kubernetes_cluster.this.kube_config_raw
  pinetools_image = local.pinetools_image
  cloud           = "azure"

  depends_on = [
    module.common,
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
    azurerm_dns_a_record.ingress,
    kubernetes_service_v1.internal_lb,
    kubernetes_service_v1.public_lb,
    kubernetes_ingress_v1.private,
    azurerm_dns_a_record.private,
    azurerm_dns_cname_record.private_cnames,
    azurerm_dns_cname_record.public_cnames,
    azurerm_key_vault_key.pulumi,
    azurerm_kubernetes_cluster.this,
    azurerm_kubernetes_cluster_node_pool.this,
    azurerm_postgresql_flexible_server_database.this,
    azurerm_role_assignment.certmanager_dns,
    azurerm_role_assignment.external_dns,
    azurerm_role_assignment.operator_keyvault,
    azurerm_role_assignment.operator_rg_contributor,
    azurerm_role_assignment.operator_storage,
    azurerm_role_assignment.storage_integration_reader,
    azurerm_storage_container.pulumi_state,
    azurerm_storage_container.this,
    azurerm_storage_management_policy.this,
    azuread_service_principal_password.storage_integration,
    kubernetes_secret_v1.external_dns_azure,
    kubernetes_secret_v1.placeholder_tls,
    kubernetes_service_account_v1.external_dns,
    pineconebyoc_amp_access.this,
    pineconebyoc_aks_api_server_waiter.this,
  ]
}
