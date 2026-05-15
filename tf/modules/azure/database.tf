resource "azurerm_private_dns_zone" "postgres" {
  name                = "pinecone.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "postgres-${local.cell_name}"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = local.tags
}

resource "random_password" "db" {
  for_each = local.dbs

  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "this" {
  for_each = local.dbs

  name                          = "${each.value.name}-${local.cell_name}"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = var.region
  version                       = "16"
  administrator_login           = each.value.username
  administrator_password        = random_password.db[each.key].result
  sku_name                      = each.value.sku_name
  storage_mb                    = 524288
  delegated_subnet_id           = azurerm_subnet.db.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false
  tags                          = local.tags

  lifecycle {
    ignore_changes = [zone, high_availability, private_dns_zone_id]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

resource "azurerm_postgresql_flexible_server_database" "this" {
  for_each = local.dbs

  name      = each.value.db_name
  server_id = azurerm_postgresql_flexible_server.this[each.key].id
  charset   = "UTF8"
}
