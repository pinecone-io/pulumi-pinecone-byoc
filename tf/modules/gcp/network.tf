resource "google_compute_network" "this" {
  name                    = "network-${local.cell_name}"
  project                 = var.project
  auto_create_subnetworks = false

  depends_on = [
    terraform_data.control_plane_ready,
    terraform_data.dns_bootstrap_ready,
  ]
}

resource "google_compute_subnetwork" "main" {
  name                     = "subnet-${local.cell_name}"
  project                  = var.project
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = var.vpc_cidr
  private_ip_google_access = true
}

resource "google_compute_subnetwork" "psc" {
  name          = "private-subnet-${local.cell_name}"
  project       = var.project
  region        = var.region
  network       = google_compute_network.this.id
  ip_cidr_range = "10.100.1.0/24"
  purpose       = "PRIVATE_SERVICE_CONNECT"
}

resource "google_compute_subnetwork" "proxy" {
  name          = "private-proxy-network-${local.cell_name}"
  project       = var.project
  region        = var.region
  network       = google_compute_network.this.id
  ip_cidr_range = "10.100.2.0/24"
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

resource "google_compute_global_address" "private_ip_range" {
  name          = "private-ip-range-${local.cell_name}"
  project       = var.project
  network       = google_compute_network.this.id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
}

resource "google_service_networking_connection" "private" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
}

resource "google_compute_router" "this" {
  name    = "router-${local.cell_name}"
  project = var.project
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  name                               = "nat-${local.cell_name}"
  project                            = var.project
  region                             = var.region
  router                             = google_compute_router.this.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
