resource "google_storage_bucket" "this" {
  for_each = toset(["data", "index-backups", "wal", "janitor", "internal"])

  name                        = "pc-${each.value}-${local.cell_name}"
  project                     = var.project
  location                    = var.region
  force_destroy               = !var.deletion_protection
  uniform_bucket_level_access = true
  labels                      = local.labels

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "AbortIncompleteMultipartUpload"
    }
    condition {
      age = 1
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_noncurrent_time = 3
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = 30
      matches_prefix = ["activity-scrapes/"]
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = 7
      matches_prefix = ["janitor/"]
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = 14
      matches_prefix = ["lag-reporter/"]
    }
  }

  depends_on = [google_container_node_pool.this]
}
