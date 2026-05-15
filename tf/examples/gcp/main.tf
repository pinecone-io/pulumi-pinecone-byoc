terraform {
  required_version = ">= 1.6.0"
}

module "pinecone" {
  source = "../../modules/gcp"

  pinecone_api_key            = var.pinecone_api_key
  pinecone_version            = var.pinecone_version
  project                     = var.project
  region                      = var.region
  availability_zones          = var.availability_zones
  vpc_cidr                    = var.vpc_cidr
  kubernetes_version          = var.kubernetes_version
  node_pools                  = var.node_pools
  parent_dns_zone_name        = var.parent_dns_zone_name
  public_access_enabled       = var.public_access_enabled
  deletion_protection         = var.deletion_protection
  api_url                     = var.api_url
  global_env                  = var.global_env
  auth0_domain                = var.auth0_domain
  amp_aws_account_id          = var.amp_aws_account_id
  labels                      = var.labels
  writer_k8s_service_accounts = var.writer_k8s_service_accounts
  reader_k8s_service_accounts = var.reader_k8s_service_accounts
}
