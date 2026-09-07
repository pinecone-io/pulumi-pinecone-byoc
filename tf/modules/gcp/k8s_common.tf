locals {
  gcp_pulumi_outputs = {
    cell_name                = local.cell_name
    org_name                 = pineconebyoc_environment.this.org_name
    cloud                    = "gcp"
    region                   = var.region
    global_env               = var.global_env
    subdomain                = pineconebyoc_environment.this.env_name
    availability_zones       = var.availability_zones
    api_url                  = var.api_url
    dns_zone_name            = google_dns_managed_zone.this.name
    gcp_k8s_version          = var.kubernetes_version
    gcp_project              = var.project
    image_registry           = local.registry_base
    sli_checkers_project_id  = pineconebyoc_project_api_key.sli.project_id
    customer_tags            = var.labels
    public_access_enabled    = var.public_access_enabled
    pulumi_backend_url       = "gs://${google_storage_bucket.pulumi_state.name}"
    pulumi_secrets_provider  = "gcpkms://${google_kms_crypto_key.pulumi_secrets.id}"
    aws_amp_region           = pineconebyoc_amp_access.this.amp_region
    aws_amp_remote_write_url = pineconebyoc_amp_access.this.amp_remote_write_endpoint
    aws_amp_sigv4_role_arn   = pineconebyoc_amp_access.this.pinecone_role_arn
    aws_amp_ingest_role_arn  = ""
    gcp_np_sa_email          = google_service_account.nodepool.email
    gcp_read_sa_email        = google_service_account.reader.email
    gcp_write_sa_email       = google_service_account.writer.email
    gcp_dns_sa_email         = google_service_account.dns.email
    gcp_pulumi_sa_email      = google_service_account.pulumi.email
    gcp_write_sa_id          = google_service_account.writer.account_id
    gcp_read_sa_id           = google_service_account.reader.account_id
    gcp_dns_sa_id            = google_service_account.dns.account_id
  }
}

module "common" {
  source = "../common"

  cloud                 = "gcp"
  cell_name             = local.cell_name
  environment           = var.global_env
  is_prod               = var.global_env == "prod"
  domain                = pineconebyoc_environment.this.env_name
  region                = var.region
  public_access_enabled = var.public_access_enabled
  api_url               = var.api_url
  registry_type         = "gcr"
  pinetools_image       = local.pinetools_image
  pinecone_version      = var.pinecone_version
  pinetools_dependency_ids = concat(
    [
      kubernetes_secret_v1.placeholder_tls.id,
      terraform_data.backend_config.id,
    ],
    var.public_access_enabled ? [
      google_compute_ssl_policy.public[0].id,
      terraform_data.frontend_config[0].id,
    ] : []
  )
  cpgw_api_key    = pineconebyoc_cpgw_api_key.this.key
  gcps_api_key    = pineconebyoc_project_api_key.sli.value
  datadog_api_key = pineconebyoc_datadog_api_key.this.api_key

  storage_integration_credentials = {
    key-json = base64decode(google_service_account_key.storage_integration.private_key)
  }

  db_credentials = {
    control = {
      host          = google_alloydb_instance.this["control"].ip_address
      readonly_host = google_alloydb_instance.this["control"].ip_address
      port          = "5432"
      username      = local.dbs.control.username
      password      = random_password.db["control"].result
      dbname        = local.dbs.control.db_name
    }
    system = {
      host          = google_alloydb_instance.this["system"].ip_address
      readonly_host = google_alloydb_instance.this["system"].ip_address
      port          = "5432"
      username      = local.dbs.system.username
      password      = random_password.db["system"].result
      dbname        = local.dbs.system.db_name
    }
  }

  pulumi_outputs = local.gcp_pulumi_outputs

  depends_on = [
    terraform_data.cloud_support_ready,
    google_container_node_pool.this,
    google_storage_bucket.this,
    google_alloydb_instance.this,
    google_dns_record_set.ingress,
    google_dns_record_set.public_cnames,
    pineconebyoc_dns_delegation.this,
    google_storage_bucket_iam_member.pulumi_state_access,
    google_kms_crypto_key_iam_member.pulumi_key_access,
    google_project_iam_member.pulumi_container_service_agent,
    google_service_account_iam_binding.pulumi_workload_identity,
    google_service_account_iam_binding.dns_workload_identity,
    google_service_account_iam_binding.reader_workload_identity,
    google_service_account_iam_member.writer_workload_identity,
  ]
}

resource "pineconebyoc_cluster_uninstaller" "this" {
  kubeconfig      = local.kubeconfig
  pinetools_image = local.pinetools_image
  cloud           = "gcp"

  depends_on = [
    module.common,
    google_alloydb_cluster.this,
    google_alloydb_instance.this,
    google_compute_global_address.external_ip,
    google_compute_global_address.private_ip_range,
    google_compute_router.this,
    google_compute_router_nat.this,
    google_compute_service_attachment.this,
    google_compute_ssl_policy.public,
    google_compute_subnetwork.main,
    google_compute_subnetwork.proxy,
    google_compute_subnetwork.psc,
    google_container_cluster.this,
    google_container_node_pool.this,
    google_dns_managed_zone.this,
    google_dns_record_set.ingress,
    google_dns_record_set.private_cnames,
    google_dns_record_set.private_ingress,
    google_dns_record_set.public_cnames,
    google_kms_crypto_key.pulumi_secrets,
    google_kms_crypto_key_iam_member.pulumi_key_access,
    google_kms_key_ring.pulumi_secrets,
    google_project_iam_member.dns_admin,
    google_project_iam_member.nodepool_service_account_admin,
    google_project_iam_member.nodepool_storage_admin,
    google_project_iam_member.pulumi_container_service_agent,
    google_project_iam_member.reader_storage_viewer,
    google_project_iam_member.storage_integration_viewer,
    google_project_iam_member.writer_service_account_admin,
    google_project_iam_member.writer_storage_admin,
    google_secret_manager_secret.db_credentials,
    google_secret_manager_secret_version.db_credentials,
    google_service_account.dns,
    google_service_account.nodepool,
    google_service_account.pulumi,
    google_service_account.reader,
    google_service_account.storage_integration,
    google_service_account.writer,
    google_service_account_iam_binding.dns_workload_identity,
    google_service_account_iam_binding.pulumi_workload_identity,
    google_service_account_iam_binding.reader_workload_identity,
    google_service_account_iam_member.writer_workload_identity,
    google_service_account_key.storage_integration,
    google_service_networking_connection.private,
    google_storage_bucket.this,
    google_storage_bucket.pulumi_state,
    google_storage_bucket_iam_member.pulumi_state_access,
    kubernetes_ingress_v1.private,
    kubernetes_ingress_v1.public,
    kubernetes_secret_v1.placeholder_tls,
    pineconebyoc_amp_access.this,
    pineconebyoc_cpgw_api_key.this,
    pineconebyoc_datadog_api_key.this,
    pineconebyoc_dns_delegation.this,
    pineconebyoc_environment.this,
    pineconebyoc_gcp_forwarding_rule_delete_waiter.private,
    pineconebyoc_gcp_forwarding_rule_waiter.private,
    kubernetes_namespace_v1.gloo_system,
    pineconebyoc_project_api_key.sli,
    pineconebyoc_service_account.this,
    random_password.db,
    terraform_data.backend_config,
    terraform_data.frontend_config,
  ]
}
