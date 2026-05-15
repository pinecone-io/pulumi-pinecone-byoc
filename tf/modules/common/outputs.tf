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

