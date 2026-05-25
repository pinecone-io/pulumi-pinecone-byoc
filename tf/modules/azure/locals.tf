locals {
  dns_cnames = ["*.svc", "metrics", "prometheus"]

  org_slug        = substr(replace(lower(pineconebyoc_environment.this.org_name), "/[^a-z0-9]/", ""), 0, 16)
  env_first_label = split(".", pineconebyoc_environment.this.env_name)[0]
  env_suffix      = substr(local.env_first_label, length(local.env_first_label) - 4, 4)
  cell_name       = "${local.org_slug}-byoc-${local.env_suffix}"
  cell_org        = split("-byoc-", local.cell_name)[0]
  cell_sa_suffix  = "-byoc-${local.env_suffix}"
  subdomain       = trimsuffix(pineconebyoc_environment.this.env_name, ".byoc")
  fqdn            = "${local.subdomain}.${var.parent_dns_zone_name}"

  tags = merge({
    "pinecone-managed-by" = "pulumi"
  }, var.tags)

  cidr_parts  = split("/", var.vpc_cidr)
  cidr_octets = split(".", local.cidr_parts[0])
  cidr_prefix = tonumber(local.cidr_parts[1])

  second_octet_increment = lookup({
    "8"  = 256
    "9"  = 128
    "10" = 64
    "11" = 32
    "12" = 16
    "13" = 8
    "14" = 4
    "15" = 2
    "16" = 1
  }, tostring(local.cidr_prefix), 0)
  third_octet_increment = lookup({
    "17" = 128
    "18" = 64
    "19" = 32
    "20" = 16
  }, tostring(local.cidr_prefix), 0)

  aks_octet_0 = local.cidr_octets[0]
  aks_octet_1 = tonumber(local.cidr_octets[1])
  aks_octet_2 = tonumber(local.cidr_octets[2])

  db_cidr = local.cidr_prefix <= 16 ? format(
    "%s.%d.0.0/%d",
    local.aks_octet_0,
    local.aks_octet_1 + local.second_octet_increment,
    local.cidr_prefix,
    ) : format(
    "%s.%d.%d.0/%d",
    local.aks_octet_0,
    local.aks_octet_1,
    local.aks_octet_2 + local.third_octet_increment,
    local.cidr_prefix,
  )

  pls_cidr = local.cidr_prefix <= 16 ? format(
    "%s.%d.0.0/27",
    local.aks_octet_0,
    local.aks_octet_1 + (2 * local.second_octet_increment),
    ) : format(
    "%s.%d.%d.0/27",
    local.aks_octet_0,
    local.aks_octet_1,
    local.aks_octet_2 + (2 * local.third_octet_increment),
  )

  flat_cell_name         = replace(local.cell_name, "-", "")
  flat_byoc_suffix       = "byoc${local.env_suffix}"
  flat_org               = split("byoc", local.flat_cell_name)[0]
  storage_account_name   = "pc${substr(local.flat_org, 0, max(0, 24 - 2 - length(local.flat_byoc_suffix)))}${local.flat_byoc_suffix}"
  key_vault_name         = "pc-${substr(local.cell_org, 0, max(0, 24 - 2 - 1 - length(local.cell_sa_suffix)))}${local.cell_sa_suffix}"
  tls_secret_name        = "${split(".", local.fqdn)[0]}-tls"
  resource_group_name    = "${local.cell_name}-${var.region}-rg"
  node_resource_group    = "${local.resource_group_name}-nodepool"
  default_node_pool      = var.node_pools[0]
  default_node_pool_name = substr(replace(replace(local.default_node_pool.name, "-", ""), "_", ""), 0, 12)

  registry_base   = "pinecone.azurecr.io/unstable/pinecone/v4"
  pinetools_image = "${local.registry_base}/pinetools:${var.pinecone_version}"

  dbs = {
    control = {
      name     = "control-db"
      db_name  = "controller"
      username = "controller"
      sku_name = "GP_Standard_D2s_v3"
    }
    system = {
      name     = "system-db"
      db_name  = "systemdb"
      username = "systemuser"
      sku_name = "GP_Standard_D2s_v3"
    }
  }
}
