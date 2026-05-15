resource "pineconebyoc_environment" "this" {
  cloud                      = "aws"
  region                     = var.region
  global_env                 = var.global_env
  api_url                    = var.api_url
  pinecone_api_key           = var.pinecone_api_key
  is_public_endpoint_enabled = var.public_access_enabled
}

resource "pineconebyoc_cpgw_api_key" "this" {
  environment      = pineconebyoc_environment.this.env_name
  api_url          = var.api_url
  pinecone_api_key = var.pinecone_api_key
  depends_on       = [pineconebyoc_environment.this]
}

resource "pineconebyoc_service_account" "this" {
  name         = "${local.cell_name}-sa"
  api_url      = var.api_url
  cpgw_api_key = pineconebyoc_cpgw_api_key.this.key
  depends_on   = [pineconebyoc_cpgw_api_key.this]
}

resource "pineconebyoc_project_api_key" "sli" {
  org_id              = pineconebyoc_environment.this.org_id
  project_name        = "__SLI__"
  key_name            = "${local.cell_name}-key"
  api_url             = var.api_url
  auth0_domain        = var.auth0_domain
  auth0_client_id     = pineconebyoc_service_account.this.client_id
  auth0_client_secret = pineconebyoc_service_account.this.client_secret
  depends_on          = [pineconebyoc_service_account.this]
}

resource "pineconebyoc_datadog_api_key" "this" {
  api_url      = var.api_url
  cpgw_api_key = pineconebyoc_cpgw_api_key.this.key
  depends_on   = [pineconebyoc_cpgw_api_key.this]
}

resource "terraform_data" "control_plane_ready" {
  input = {
    environment_id     = pineconebyoc_environment.this.id
    cpgw_key_id        = pineconebyoc_cpgw_api_key.this.key_id
    service_account_id = pineconebyoc_service_account.this.id
    sli_project_id     = pineconebyoc_project_api_key.sli.project_id
    datadog_key_id     = pineconebyoc_datadog_api_key.this.key_id
  }

  depends_on = [
    pineconebyoc_environment.this,
    pineconebyoc_cpgw_api_key.this,
    pineconebyoc_service_account.this,
    pineconebyoc_project_api_key.sli,
    pineconebyoc_datadog_api_key.this,
  ]
}
