output "control_plane_namespace" {
  value = kubernetes_namespace_v1.control_plane.metadata[0].name
}

output "pinetools_service_account" {
  value = kubernetes_service_account_v1.pinetools.metadata[0].name
}

output "pinetools_cluster_role_binding" {
  value = kubernetes_cluster_role_binding_v1.pinetools.metadata[0].name
}

output "pinetools_install_job_name" {
  value = kubernetes_job_v1.pinetools_install.metadata[0].name
}

output "registry_refresher_name" {
  value = kubernetes_cron_job_v1.registry_refresher.metadata[0].name
}

output "cluster_uninstaller_dependency_ids" {
  value = concat(
    [
      kubernetes_namespace_v1.external_secrets.id,
      kubernetes_secret_v1.cpgw_credentials.id,
      kubernetes_secret_v1.gcps_api_key.id,
      kubernetes_secret_v1.datadog_api_key.id,
      kubernetes_secret_v1.exdb_control.id,
      kubernetes_secret_v1.exdb_system.id,
      kubernetes_secret_v1.exdb_data.id,
      kubernetes_secret_v1.exdb_all.id,
      kubernetes_namespace_v1.cluster_information.id,
      kubernetes_config_map_v1.cluster_information.id,
      kubernetes_namespace_v1.pulumi_outputs.id,
      kubernetes_config_map_v1.pulumi_outputs.id,
      kubernetes_cluster_role_v1.registry_refresher.id,
      kubernetes_service_account_v1.registry_refresher.id,
      kubernetes_cluster_role_binding_v1.registry_refresher.id,
      kubernetes_config_map_v1.registry_refresher.id,
      kubernetes_cron_job_v1.registry_refresher.id,
      kubernetes_namespace_v1.control_plane.id,
      kubernetes_service_account_v1.pinetools.id,
      kubernetes_cluster_role_binding_v1.pinetools.id,
      kubernetes_cron_job_v1.pinetools.id,
    ],
    [for secret in kubernetes_secret_v1.azure_storage_key : secret.id],
    [for secret in kubernetes_secret_v1.storage_integration_credentials : secret.id],
  )
}
