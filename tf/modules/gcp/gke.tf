resource "google_container_cluster" "this" {
  provider = google-beta

  name                     = local.cluster_name
  location                 = var.region
  node_locations           = var.availability_zones
  network                  = google_compute_network.this.id
  subnetwork               = google_compute_subnetwork.main.id
  networking_mode          = "VPC_NATIVE"
  datapath_provider        = "ADVANCED_DATAPATH"
  initial_node_count       = 1
  remove_default_node_pool = true
  deletion_protection      = var.deletion_protection
  resource_labels          = local.labels

  ip_allocation_policy {
    cluster_ipv4_cidr_block  = "/14"
    services_ipv4_cidr_block = "/18"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "10.100.0.0/28"
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = "0.0.0.0/0"
      display_name = "All networks"
    }
  }

  workload_identity_config {
    workload_pool = "${var.project}.svc.id.goog"
  }

  addons_config {
    dns_cache_config {
      enabled = true
    }
  }

  binary_authorization {
    evaluation_mode = "PROJECT_SINGLETON_POLICY_ENFORCE"
  }

  release_channel {
    channel = "UNSPECIFIED"
  }

  cluster_autoscaling {
    enabled             = false
    autoscaling_profile = "OPTIMIZE_UTILIZATION"
  }

  monitoring_config {
    managed_prometheus {
      enabled = false
    }
  }

  depends_on = [
    google_compute_router_nat.this,
    google_service_networking_connection.private,
  ]
}

resource "time_sleep" "workload_identity_pool_ready" {
  create_duration = "120s"

  depends_on = [google_container_cluster.this]
}

resource "google_service_account" "nodepool" {
  account_id   = local.np_sa_account_id
  display_name = "Nodepool service account for ${local.cell_name}"

  depends_on = [google_service_networking_connection.private]
}

resource "google_service_account" "reader" {
  account_id   = local.reader_sa_account_id
  display_name = "Reader service account for ${local.cell_name}"

  depends_on = [google_service_networking_connection.private]
}

resource "google_service_account" "writer" {
  account_id   = local.writer_sa_account_id
  display_name = "Writer service account for ${local.cell_name}"

  depends_on = [google_service_networking_connection.private]
}

resource "google_service_account" "dns" {
  account_id   = local.dns_sa_account_id
  display_name = "DNS service account for ${local.cell_name}"

  depends_on = [google_service_networking_connection.private]
}

resource "google_service_account" "pulumi" {
  account_id   = local.pulumi_sa_account_id
  display_name = "Pulumi service account for ${local.cell_name}"

  depends_on = [google_service_networking_connection.private]
}

resource "google_service_account" "storage_integration" {
  account_id   = local.storage_sa_account_id
  display_name = "Storage integration service account for ${local.cell_name}"

  depends_on = [google_service_networking_connection.private]
}

resource "google_project_iam_member" "nodepool_service_account_admin" {
  project = var.project
  role    = "roles/iam.serviceAccountAdmin"
  member  = "serviceAccount:${google_service_account.nodepool.email}"
}

resource "google_project_iam_member" "nodepool_storage_admin" {
  project = var.project
  role    = "roles/storage.admin"
  member  = "serviceAccount:${google_service_account.nodepool.email}"
}

resource "google_project_iam_member" "reader_storage_viewer" {
  project = var.project
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${google_service_account.reader.email}"
}

resource "google_project_iam_member" "writer_storage_admin" {
  project = var.project
  role    = "roles/storage.admin"
  member  = "serviceAccount:${google_service_account.writer.email}"
}

resource "google_project_iam_member" "writer_service_account_admin" {
  project = var.project
  role    = "roles/iam.serviceAccountAdmin"
  member  = "serviceAccount:${google_service_account.writer.email}"
}

resource "google_project_iam_member" "dns_admin" {
  project = var.project
  role    = "roles/dns.admin"
  member  = "serviceAccount:${google_service_account.dns.email}"
}

resource "google_project_iam_member" "pulumi_container_service_agent" {
  project = var.project
  role    = "roles/container.serviceAgent"
  member  = "serviceAccount:${google_service_account.pulumi.email}"
}

resource "google_project_iam_member" "storage_integration_viewer" {
  project = var.project
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${google_service_account.storage_integration.email}"
}

resource "google_service_account_iam_binding" "dns_workload_identity" {
  service_account_id = google_service_account.dns.name
  role               = "roles/iam.workloadIdentityUser"
  members            = ["serviceAccount:${var.project}.svc.id.goog[gloo-system/certmanager-certgen]"]

  depends_on = [time_sleep.workload_identity_pool_ready]
}

resource "google_service_account_iam_binding" "pulumi_workload_identity" {
  service_account_id = google_service_account.pulumi.name
  role               = "roles/iam.workloadIdentityUser"
  members            = ["serviceAccount:${var.project}.svc.id.goog[pulumi-kubernetes-operator/pulumi-k8s-operator]"]

  depends_on = [time_sleep.workload_identity_pool_ready]
}

resource "google_service_account_iam_member" "writer_workload_identity" {
  for_each           = toset(var.writer_k8s_service_accounts)
  service_account_id = google_service_account.writer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project}.svc.id.goog[${each.value}]"

  depends_on = [time_sleep.workload_identity_pool_ready]
}

resource "google_service_account_iam_binding" "reader_workload_identity" {
  service_account_id = google_service_account.reader.name
  role               = "roles/iam.workloadIdentityUser"
  members            = [for sa in var.reader_k8s_service_accounts : "serviceAccount:${var.project}.svc.id.goog[${sa}]"]

  depends_on = [time_sleep.workload_identity_pool_ready]
}

resource "google_service_account_key" "storage_integration" {
  service_account_id = google_service_account.storage_integration.name
}

resource "google_container_node_pool" "this" {
  for_each = { for node_pool in var.node_pools : node_pool.name => node_pool }

  name               = substr("${local.resource_prefix}-gke-np-${each.value.name}", 0, 32)
  location           = var.region
  cluster            = google_container_cluster.this.name
  node_locations     = var.availability_zones
  initial_node_count = each.value.min_size

  autoscaling {
    min_node_count  = each.value.min_size
    max_node_count  = each.value.max_size
    location_policy = "BALANCED"
  }

  node_config {
    machine_type     = each.value.machine_type
    min_cpu_platform = "Intel Ice Lake"
    disk_size_gb     = each.value.disk_size_gb
    service_account  = google_service_account.nodepool.email
    oauth_scopes     = ["https://www.googleapis.com/auth/cloud-platform"]
    labels           = merge({ "pinecone.io/cell" = local.cell_name, nodepool_name = each.value.name }, each.value.labels, local.labels)
    resource_labels  = local.labels

    dynamic "taint" {
      for_each = each.value.taints
      content {
        key    = taint.value.key
        value  = taint.value.value
        effect = taint.value.effect
      }
    }
  }

  management {
    auto_repair  = false
    auto_upgrade = false
  }

  lifecycle {
    ignore_changes = [initial_node_count, node_count]
  }

  depends_on = [
    google_project_iam_member.nodepool_service_account_admin,
    google_project_iam_member.nodepool_storage_admin,
    google_service_account_iam_binding.dns_workload_identity,
    google_service_account_iam_binding.pulumi_workload_identity,
    google_service_account_iam_binding.reader_workload_identity,
    google_service_account_iam_member.writer_workload_identity,
  ]
}
