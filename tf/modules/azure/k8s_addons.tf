locals {
  dns_zone_contributor_role = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/befefa01-2a29-4197-83a8-272ff33ce314"
}

resource "kubernetes_namespace_v1" "gloo_system" {
  metadata {
    name = "gloo-system"
    labels = {
      "kubernetes.io/metadata.name" = "gloo-system"
      name                          = "gloo-system"
    }
  }

  depends_on = [pineconebyoc_aks_api_server_waiter.this]
}

resource "azurerm_user_assigned_identity" "external_dns" {
  name                = "external-dns-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "external_dns" {
  name                = "external-dns-fic-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.external_dns.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:gloo-system:external-dns"

  depends_on = [time_sleep.workload_identity_ready]
}

resource "azurerm_role_assignment" "external_dns" {
  scope              = azurerm_dns_zone.this.id
  role_definition_id = local.dns_zone_contributor_role
  principal_id       = azurerm_user_assigned_identity.external_dns.principal_id
  principal_type     = "ServicePrincipal"
}

resource "kubernetes_service_account_v1" "external_dns" {
  metadata {
    name      = "external-dns"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    labels = {
      "azure.workload.identity/use" = "true"
    }
    annotations = {
      "azure.workload.identity/client-id" = azurerm_user_assigned_identity.external_dns.client_id
    }
  }
}

resource "kubernetes_secret_v1" "external_dns_azure" {
  metadata {
    name      = "external-dns-azure"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    annotations = {
      "pulumi.com/patchForce" = "true"
    }
  }

  data = {
    "azure.json" = jsonencode({
      tenantId                     = data.azurerm_client_config.current.tenant_id
      subscriptionId               = var.subscription_id
      resourceGroup                = azurerm_resource_group.this.name
      useWorkloadIdentityExtension = true
    })
  }
}

resource "azurerm_user_assigned_identity" "certmanager" {
  name                = "certmanager-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "certmanager" {
  name                = "certmanager-fic-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.certmanager.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:gloo-system:certmanager-certgen"

  depends_on = [time_sleep.workload_identity_ready]
}

resource "azurerm_role_assignment" "certmanager_dns" {
  scope              = azurerm_dns_zone.this.id
  role_definition_id = local.dns_zone_contributor_role
  principal_id       = azurerm_user_assigned_identity.certmanager.principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_user_assigned_identity" "prometheus" {
  name                = "prometheus-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "prometheus" {
  name                = "prometheus-fic-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.prometheus.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:prometheus:amp-iamproxy-ingest-service-account"

  depends_on = [time_sleep.workload_identity_ready]
}
