resource "terraform_data" "cloud_support_ready" {
  input = {
    cluster_id = google_container_cluster.this.id
    node_pool_ids = {
      for name, pool in google_container_node_pool.this : name => pool.id
    }
    db_instance_ids = {
      for name, instance in google_alloydb_instance.this : name => instance.id
    }
    storage_bucket_ids = {
      for name, bucket in google_storage_bucket.this : name => bucket.id
    }
    pulumi_state_bucket = google_storage_bucket.pulumi_state.id
  }

  depends_on = [
    google_alloydb_instance.this,
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
    google_storage_bucket.this,
    google_storage_bucket.pulumi_state,
    google_storage_bucket_iam_member.pulumi_state_access,
  ]
}
