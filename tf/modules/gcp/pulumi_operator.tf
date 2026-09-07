resource "google_storage_bucket" "pulumi_state" {
  name                        = "pc-pulumi-state-${local.cell_name}"
  project                     = var.project
  location                    = var.region
  force_destroy               = true
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
      age = 2
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      num_newer_versions = 30
      with_state         = "ARCHIVED"
    }
  }

  depends_on = [google_container_node_pool.this]
}

resource "google_kms_key_ring" "pulumi_secrets" {
  name     = "pulumi-secrets-${local.cell_name}"
  project  = var.project
  location = var.region

  depends_on = [google_container_node_pool.this]
}

resource "google_kms_crypto_key" "pulumi_secrets" {
  name            = "pulumi-secrets"
  key_ring        = google_kms_key_ring.pulumi_secrets.id
  rotation_period = "7776000s"
  purpose         = "ENCRYPT_DECRYPT"
  labels          = local.labels
}

resource "google_storage_bucket_iam_member" "pulumi_state_access" {
  bucket = google_storage_bucket.pulumi_state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pulumi.email}"
}

resource "google_kms_crypto_key_iam_member" "pulumi_key_access" {
  crypto_key_id = google_kms_crypto_key.pulumi_secrets.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.pulumi.email}"
}
