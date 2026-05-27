terraform {
  required_version = ">= 1.6.0"
}

module "pinecone" {
  source = "../../modules/azure"

  pinecone_api_key      = var.pinecone_api_key
  pinecone_version      = var.pinecone_version
  subscription_id       = var.subscription_id
  region                = var.region
  availability_zones    = var.availability_zones
  vpc_cidr              = var.vpc_cidr
  kubernetes_version    = var.kubernetes_version
  node_pools            = var.node_pools
  parent_dns_zone_name  = var.parent_dns_zone_name
  public_access_enabled = var.public_access_enabled
  api_url               = var.api_url
  global_env            = var.global_env
  auth0_domain          = var.auth0_domain
  tags                  = var.tags
}
