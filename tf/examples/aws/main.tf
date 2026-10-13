terraform {
  required_version = ">= 1.6.0"
}

module "pinecone" {
  source = "../../modules/aws"

  pinecone_api_key      = var.pinecone_api_key
  pinecone_version      = var.pinecone_version
  region                = var.region
  availability_zones    = var.availability_zones
  vpc_cidr              = var.vpc_cidr
  kubernetes_version    = var.kubernetes_version
  node_pools            = var.node_pools
  parent_dns_zone_name  = var.parent_dns_zone_name
  public_access_enabled = var.public_access_enabled
  deletion_protection   = var.deletion_protection
  api_url               = var.api_url
  global_env            = var.global_env
  auth0_domain          = var.auth0_domain
  custom_ami_id         = var.custom_ami_id
  kms_key_arn           = var.kms_key_arn
  tags                  = var.tags
}
