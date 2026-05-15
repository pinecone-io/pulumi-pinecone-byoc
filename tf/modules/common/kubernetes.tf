resource "kubernetes_namespace_v1" "external_secrets" {
  metadata {
    name = local.external_ns
    labels = {
      "kubernetes.io/metadata.name" = local.external_ns
      name                          = local.external_ns
    }
  }
}

resource "kubernetes_secret_v1" "cpgw_credentials" {
  metadata {
    name      = "cpgw-credentials"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = {
    "api-key" = var.cpgw_api_key
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "gcps_api_key" {
  metadata {
    name      = "gcps-api-key"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = {
    "api-key" = var.gcps_api_key
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "datadog_api_key" {
  metadata {
    name      = "datadog-api-key"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = {
    "api-key" = var.datadog_api_key
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "azure_storage_key" {
  count = var.azure_storage_access_key == null ? 0 : 1
  metadata {
    name      = "azure-storage-account-access-key"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = {
    key = var.azure_storage_access_key
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "storage_integration_credentials" {
  count = length(var.storage_integration_credentials) == 0 ? 0 : 1
  metadata {
    name      = "storage-integration-credentials"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = var.storage_integration_credentials
  type = "Opaque"
}

resource "kubernetes_secret_v1" "exdb_control" {
  metadata {
    name      = "exdb-control-db-credentials"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = local.control_db_secret
  type = "Opaque"
}

resource "kubernetes_secret_v1" "exdb_system" {
  metadata {
    name      = "exdb-system-db-credentials"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = local.system_db_secret
  type = "Opaque"
}

resource "kubernetes_secret_v1" "exdb_data" {
  metadata {
    name      = "exdb-data-db-credentials"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = local.control_db_secret
  type = "Opaque"
}

resource "kubernetes_secret_v1" "exdb_all" {
  metadata {
    name      = "exdb-all-credentials"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = {
    shards = local.shards_json
  }
  type = "Opaque"
}

resource "kubernetes_namespace_v1" "cluster_information" {
  metadata {
    name = "pc-cluster-information"
    labels = {
      name = "pc-cluster-information"
    }
  }
}

resource "kubernetes_config_map_v1" "cluster_information" {
  metadata {
    name      = "config"
    namespace = kubernetes_namespace_v1.cluster_information.metadata[0].name
  }
  data = {
    cloud                 = var.cloud
    cell_name             = var.cell_name
    env                   = var.environment
    is_prod               = tostring(var.is_prod)
    domain                = var.domain
    region                = var.region
    public_access_enabled = tostring(var.public_access_enabled)
  }
}

resource "kubernetes_namespace_v1" "pulumi_outputs" {
  metadata {
    name = "pc-pulumi-outputs"
    labels = {
      name = "pc-pulumi-outputs"
    }
  }
}

resource "kubernetes_config_map_v1" "pulumi_outputs" {
  metadata {
    name      = "config"
    namespace = kubernetes_namespace_v1.pulumi_outputs.metadata[0].name
  }
  data = {
    "pulumi-outputs" = jsonencode(var.pulumi_outputs)
  }
}

resource "kubernetes_cluster_role_v1" "registry_refresher" {
  metadata {
    name = "${var.registry_type}-credential-refresher"
  }
  rule {
    api_groups = [""]
    resources  = ["secrets"]
    verbs      = ["create", "delete", "patch", "update", "get"]
  }
  rule {
    api_groups = [""]
    resources  = ["namespaces"]
    verbs      = ["list", "get"]
  }
}

resource "kubernetes_service_account_v1" "registry_refresher" {
  metadata {
    name      = "${var.registry_type}-credential-refresher"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
}

resource "kubernetes_cluster_role_binding_v1" "registry_refresher" {
  metadata {
    name = "${var.registry_type}-credential-refresher"
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.registry_refresher.metadata[0].name
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.registry_refresher.metadata[0].name
  }
}

resource "kubernetes_config_map_v1" "registry_refresher" {
  metadata {
    name      = "${var.registry_type}-refresher-config"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  data = {
    cpgw-url = var.api_url
  }
}

resource "kubernetes_cron_job_v1" "registry_refresher" {
  metadata {
    name      = "${var.registry_type}-credential-refresher"
    namespace = kubernetes_namespace_v1.external_secrets.metadata[0].name
  }
  spec {
    schedule                      = "* * * * *"
    successful_jobs_history_limit = 3
    failed_jobs_history_limit     = 3
    concurrency_policy            = "Forbid"
    job_template {
      metadata {}
      spec {
        backoff_limit              = 1
        ttl_seconds_after_finished = 300
        template {
          metadata {}
          spec {
            service_account_name = kubernetes_service_account_v1.registry_refresher.metadata[0].name
            restart_policy       = "OnFailure"
            container {
              name    = "${var.registry_type}-credential-refresher"
              image   = "alpine/k8s:1.31.3"
              command = ["/bin/sh", "-c"]
              args    = [local.registry_refresher_script]
              env {
                name = "CPGW_API_KEY"
                value_from {
                  secret_key_ref {
                    name = kubernetes_secret_v1.cpgw_credentials.metadata[0].name
                    key  = "api-key"
                  }
                }
              }
              env {
                name = "CPGW_URL"
                value_from {
                  config_map_key_ref {
                    name = kubernetes_config_map_v1.registry_refresher.metadata[0].name
                    key  = "cpgw-url"
                  }
                }
              }
              env {
                name  = "EXTRA_NAMESPACES"
                value = local.extra_namespaces
              }
            }
          }
        }
      }
    }
  }
}

resource "kubernetes_namespace_v1" "control_plane" {
  metadata {
    name = local.control_plane_ns
  }
}

resource "kubernetes_service_account_v1" "pinetools" {
  metadata {
    name      = "pinetools"
    namespace = kubernetes_namespace_v1.control_plane.metadata[0].name
  }
  image_pull_secret {
    name = "regcred"
  }
}

resource "kubernetes_cluster_role_binding_v1" "pinetools" {
  metadata {
    name = "pinetools-cluster-admin"
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.pinetools.metadata[0].name
    namespace = kubernetes_namespace_v1.control_plane.metadata[0].name
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "cluster-admin"
  }
}

resource "kubernetes_cron_job_v1" "pinetools" {
  metadata {
    name      = "pinetools"
    namespace = kubernetes_namespace_v1.control_plane.metadata[0].name
  }
  spec {
    suspend                       = true
    schedule                      = "0 * * * *"
    successful_jobs_history_limit = 3
    failed_jobs_history_limit     = 3
    concurrency_policy            = "Forbid"
    job_template {
      metadata {}
      spec {
        backoff_limit              = 0
        ttl_seconds_after_finished = 3600
        template {
          metadata {}
          spec {
            service_account_name = kubernetes_service_account_v1.pinetools.metadata[0].name
            restart_policy       = "OnFailure"
            toleration {
              key      = "node.kubernetes.io/disk-pressure"
              operator = "Exists"
              effect   = "NoSchedule"
            }
            container {
              name    = "pinetools"
              image   = var.pinetools_image
              command = ["/bin/sh", "-c"]
              args    = ["pinetools cluster install && pinetools cluster check"]
              env {
                name  = "PINECONE_IMAGE_VERSION"
                value = var.pinecone_version
              }
              resources {
                requests = {
                  ephemeral-storage = "1Gi"
                  memory            = "512Mi"
                  cpu               = "100m"
                }
                limits = {
                  ephemeral-storage = "5Gi"
                  memory            = "2Gi"
                }
              }
            }
          }
        }
      }
    }
  }
}

resource "terraform_data" "pinetools_dependencies" {
  input = var.pinetools_dependency_ids
}

resource "kubernetes_job_v1" "pinetools_install" {
  metadata {
    name      = local.pinetools_job_name
    namespace = kubernetes_namespace_v1.control_plane.metadata[0].name
  }
  spec {
    backoff_limit              = 1
    active_deadline_seconds    = 1800
    ttl_seconds_after_finished = 300
    template {
      metadata {}
      spec {
        service_account_name = kubernetes_service_account_v1.pinetools.metadata[0].name
        restart_policy       = "OnFailure"
        toleration {
          key      = "node.kubernetes.io/disk-pressure"
          operator = "Exists"
          effect   = "NoSchedule"
        }
        init_container {
          name    = "wait-for-regcred"
          image   = "alpine/k8s:1.31.3"
          command = ["/bin/sh", "-c"]
          args    = [local.wait_for_regcred_script]
        }
        container {
          name    = "pinetools"
          image   = var.pinetools_image
          command = ["/bin/sh", "-c"]
          args    = ["pinetools cluster install && pinetools cluster check"]
          env {
            name  = "PINECONE_IMAGE_VERSION"
            value = var.pinecone_version
          }
          resources {
            requests = {
              ephemeral-storage = "1Gi"
              memory            = "512Mi"
              cpu               = "100m"
            }
            limits = {
              ephemeral-storage = "5Gi"
              memory            = "2Gi"
            }
          }
        }
      }
    }
  }
  wait_for_completion = true
  depends_on = [
    kubernetes_cron_job_v1.registry_refresher,
    kubernetes_cron_job_v1.pinetools,
    terraform_data.pinetools_dependencies,
  ]

  timeouts {
    create = "30m"
    update = "30m"
  }
}
