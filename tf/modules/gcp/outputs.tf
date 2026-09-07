output "cluster_name" {
  value = local.cell_name
}

output "region" {
  value = var.region
}

output "organization_id" {
  value = pineconebyoc_environment.this.org_id
}

output "organization_name" {
  value = pineconebyoc_environment.this.org_name
}

output "vpc_id" {
  value = google_compute_network.this.id
}

output "cluster_endpoint" {
  value = google_container_cluster.this.endpoint
}

output "kubeconfig" {
  value     = local.kubeconfig
  sensitive = true
}

output "data_bucket" {
  value = google_storage_bucket.this["data"].name
}

output "control_db_endpoint" {
  value = google_alloydb_instance.this["control"].ip_address
}

output "system_db_endpoint" {
  value = google_alloydb_instance.this["system"].ip_address
}

output "environment_id" {
  value = pineconebyoc_environment.this.id
}

output "environment_name" {
  value = pineconebyoc_environment.this.env_name
}

output "service_account_id" {
  value = pineconebyoc_service_account.this.id
}

output "service_account_client_id" {
  value = pineconebyoc_service_account.this.client_id
}

output "api_key_project_id" {
  value = pineconebyoc_project_api_key.sli.project_id
}

output "subdomain" {
  value = pineconebyoc_environment.this.env_name
}

output "sli_checkers_project_id" {
  value = pineconebyoc_project_api_key.sli.project_id
}

output "cpgw_api_key" {
  value     = pineconebyoc_cpgw_api_key.this.key
  sensitive = true
}

output "cpgw_admin_api_key_id" {
  value = pineconebyoc_cpgw_api_key.this.key_id
}

output "datadog_api_key_id" {
  value = pineconebyoc_datadog_api_key.this.key_id
}

output "customer_tags" {
  value = var.labels
}

output "pulumi_backend_url" {
  value = "gs://${google_storage_bucket.pulumi_state.name}"
}

output "pulumi_secrets_provider" {
  value = "gcpkms://${google_kms_crypto_key.pulumi_secrets.id}"
}

output "psc_service_attachment" {
  value = google_compute_service_attachment.this.self_link
}
