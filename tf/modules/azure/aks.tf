resource "azurerm_user_assigned_identity" "cluster" {
  name                = "${local.cell_name}-cluster-identity"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "kubelet" {
  name                = "${local.cell_name}-kubelet-identity"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  tags                = local.tags
}

resource "azurerm_role_assignment" "mi_operator" {
  scope                = azurerm_user_assigned_identity.kubelet.id
  role_definition_name = "Managed Identity Operator"
  principal_id         = azurerm_user_assigned_identity.cluster.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "network_contributor_subnet" {
  scope                = azurerm_subnet.aks.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.cluster.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "network_contributor_rg" {
  scope                = azurerm_resource_group.this.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.cluster.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_kubernetes_cluster" "this" {
  name                              = "cluster-${local.cell_name}"
  resource_group_name               = azurerm_resource_group.this.name
  location                          = var.region
  kubernetes_version                = var.kubernetes_version
  dns_prefix                        = local.cell_name
  role_based_access_control_enabled = true
  node_resource_group               = local.node_resource_group
  sku_tier                          = "Standard"
  oidc_issuer_enabled               = true
  workload_identity_enabled         = true
  tags                              = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.cluster.id]
  }

  kubelet_identity {
    client_id                 = azurerm_user_assigned_identity.kubelet.client_id
    object_id                 = azurerm_user_assigned_identity.kubelet.principal_id
    user_assigned_identity_id = azurerm_user_assigned_identity.kubelet.id
  }

  default_node_pool {
    name                 = local.default_node_pool_name
    vm_size              = local.default_node_pool.vm_size
    os_sku               = "Ubuntu"
    type                 = "VirtualMachineScaleSets"
    auto_scaling_enabled = true
    min_count            = local.default_node_pool.min_size
    max_count            = local.default_node_pool.max_size
    node_count           = local.default_node_pool.min_size
    os_disk_size_gb      = local.default_node_pool.disk_size_gb
    vnet_subnet_id       = azurerm_subnet.aks.id
    zones                = var.availability_zones
    node_labels          = merge({ nodepool_name = local.default_node_pool.name }, local.default_node_pool.labels)
  }

  network_profile {
    network_plugin = "azure"
    dns_service_ip = "112.0.0.10"
    service_cidr   = "112.0.0.0/16"
  }

  auto_scaler_profile {
    balance_similar_node_groups   = true
    skip_nodes_with_local_storage = false
  }

  lifecycle {
    ignore_changes = [default_node_pool[0].node_count]
  }

  depends_on = [
    azurerm_role_assignment.mi_operator,
    azurerm_role_assignment.network_contributor_subnet,
  ]
}

resource "azurerm_kubernetes_cluster_node_pool" "this" {
  for_each = {
    for idx, node_pool in var.node_pools : node_pool.name => node_pool
    if idx != 0
  }

  name                  = substr(replace(replace(each.value.name, "-", ""), "_", ""), 0, 12)
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = each.value.vm_size
  os_sku                = "Ubuntu"
  mode                  = "User"
  auto_scaling_enabled  = true
  min_count             = each.value.min_size
  max_count             = each.value.max_size
  node_count            = each.value.min_size
  os_disk_size_gb       = each.value.disk_size_gb
  vnet_subnet_id        = azurerm_subnet.aks.id
  zones                 = var.availability_zones
  node_labels           = merge({ nodepool_name = each.value.name }, each.value.labels)
  node_taints           = [for taint in each.value.taints : "${taint.key}=${taint.value}:${taint.effect}"]
  tags                  = local.tags

  lifecycle {
    ignore_changes = [node_count]
  }
}

resource "pineconebyoc_aks_api_server_waiter" "this" {
  kubeconfig = azurerm_kubernetes_cluster.this.kube_config_raw
}
