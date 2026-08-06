locals {
  required_services = toset([
    "alloydb.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "dns.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sts.googleapis.com",
    "storage.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project = var.project
  service = each.value

  disable_on_destroy = false
}

resource "terraform_data" "gcp_apis_ready" {
  input = {
    services = sort([for service in google_project_service.required : service.service])
  }

  depends_on = [google_project_service.required]
}
