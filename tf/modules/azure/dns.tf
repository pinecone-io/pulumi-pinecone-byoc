resource "azurerm_public_ip" "external" {
  name                = "externalip-${local.cell_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.region
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_dns_zone" "this" {
  name                = local.fqdn
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_dns_a_record" "ingress" {
  name                = "ingress"
  zone_name           = azurerm_dns_zone.this.name
  resource_group_name = azurerm_resource_group.this.name
  ttl                 = 300
  records             = [azurerm_public_ip.external.ip_address]
}

resource "azurerm_dns_cname_record" "public_cnames" {
  for_each = toset(local.dns_cnames)

  name                = each.value
  zone_name           = azurerm_dns_zone.this.name
  resource_group_name = azurerm_resource_group.this.name
  ttl                 = 300
  record              = "ingress.${local.fqdn}"
}

resource "pineconebyoc_dns_delegation" "this" {
  subdomain    = local.subdomain
  nameservers  = azurerm_dns_zone.this.name_servers
  api_url      = var.api_url
  cpgw_api_key = pineconebyoc_cpgw_api_key.this.key
  depends_on   = [azurerm_dns_zone.this, pineconebyoc_cpgw_api_key.this]
}

resource "terraform_data" "dns_bootstrap_ready" {
  input = {
    external_ip_id = azurerm_public_ip.external.id
    zone_id        = azurerm_dns_zone.this.id
    ingress_id     = azurerm_dns_a_record.ingress.id
  }

  depends_on = [
    azurerm_dns_a_record.ingress,
    azurerm_dns_cname_record.public_cnames,
    azurerm_public_ip.external,
    pineconebyoc_dns_delegation.this,
  ]
}
