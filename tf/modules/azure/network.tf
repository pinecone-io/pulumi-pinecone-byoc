resource "azurerm_resource_group" "this" {
  name     = local.resource_group_name
  location = var.region
  tags     = local.tags

  depends_on = [terraform_data.control_plane_ready]
}

resource "azurerm_public_ip" "nat" {
  name                = "nat-ip-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_nat_gateway" "this" {
  name                = "nat-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  sku_name            = "Standard"
  tags                = local.tags
}

resource "azurerm_nat_gateway_public_ip_association" "this" {
  nat_gateway_id       = azurerm_nat_gateway.this.id
  public_ip_address_id = azurerm_public_ip.nat.id
}

resource "azurerm_virtual_network" "this" {
  name                = "vnet-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  address_space       = [var.vpc_cidr, local.db_cidr, local.pls_cidr]
  tags                = local.tags
}

resource "azurerm_subnet" "aks" {
  name                 = "aks-subnet-${local.cell_name}"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.vpc_cidr]
  service_endpoints    = ["Microsoft.Storage"]
}

resource "azurerm_subnet_nat_gateway_association" "aks" {
  subnet_id      = azurerm_subnet.aks.id
  nat_gateway_id = azurerm_nat_gateway.this.id
}

resource "azurerm_subnet" "db" {
  name                 = "db-subnet-${local.cell_name}"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [local.db_cidr]

  delegation {
    name = "postgresql-delegation"
    service_delegation {
      name = "Microsoft.DBforPostgreSQL/flexibleServers"
    }
  }
}

resource "azurerm_subnet" "pls" {
  name                                          = "pls-subnet-${local.cell_name}"
  resource_group_name                           = azurerm_resource_group.this.name
  virtual_network_name                          = azurerm_virtual_network.this.name
  address_prefixes                              = [local.pls_cidr]
  private_link_service_network_policies_enabled = false
}
