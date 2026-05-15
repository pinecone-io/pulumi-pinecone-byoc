locals {
  resource_prefix = "pc"
  dns_cnames      = ["*.svc", "metrics", "prometheus"]

  org_slug        = substr(replace(lower(pineconebyoc_environment.this.org_name), "/[^a-z0-9]/", ""), 0, 16)
  env_first_label = split(".", pineconebyoc_environment.this.env_name)[0]
  env_suffix      = substr(local.env_first_label, length(local.env_first_label) - 4, 4)
  cell_name       = "${local.org_slug}-byoc-${local.env_suffix}"
  resource_suffix = substr(local.cell_name, length(local.cell_name) - 4, 4)
  subdomain       = trimsuffix(pineconebyoc_environment.this.env_name, ".byoc")
  fqdn            = "${local.subdomain}.${var.parent_dns_zone_name}"

  tags = merge({
    "pinecone:managed-by" = "pulumi"
  }, var.tags)

  registry_base        = "843333058014.dkr.ecr.us-east-1.amazonaws.com/unstable/pinecone/v4"
  pinetools_image      = "${local.registry_base}/pinetools:${var.pinecone_version}"
  cluster_name         = substr("cluster-${local.cell_name}", 0, 100)
  public_subnet_cidrs  = [for i, _ in var.availability_zones : cidrsubnet(var.vpc_cidr, 4, i)]
  private_subnet_cidrs = [for i, _ in var.availability_zones : cidrsubnet(var.vpc_cidr, 2, i + 1)]

  dbs = {
    control = {
      name           = "control-db"
      db_name        = "controller"
      username       = "controller"
      instance_class = "db.r8g.large"
    }
    system = {
      name           = "system-db"
      db_name        = "systemdb"
      username       = "systemuser"
      instance_class = "db.r8g.large"
    }
  }
}
