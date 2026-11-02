resource "random_password" "db" {
  for_each = local.dbs

  length     = 32
  special    = false
  depends_on = [google_service_networking_connection.private]
}

resource "google_alloydb_cluster" "this" {
  for_each = local.dbs

  cluster_id = "${each.value.name}-${local.cell_name}"
  location   = var.region
  project    = var.project
  labels     = local.labels

  network_config {
    network            = google_compute_network.this.id
    allocated_ip_range = google_compute_global_address.private_ip_range.name
  }

  initial_user {
    user     = each.value.username
    password = random_password.db[each.key].result
  }

  deletion_policy = var.deletion_protection ? "DEFAULT" : "FORCE"
  depends_on      = [google_service_networking_connection.private]
}

resource "google_alloydb_instance" "this" {
  for_each = local.dbs

  cluster           = google_alloydb_cluster.this[each.key].name
  instance_id       = "${each.value.name}-${local.cell_name}-instance"
  instance_type     = "PRIMARY"
  availability_type = var.deletion_protection ? "REGIONAL" : "ZONAL"
  labels            = local.labels

  machine_config {
    cpu_count = each.value.cpu_count
  }

  database_flags = {
    max_connections = each.value.cpu_count >= 8 ? "4000" : each.value.cpu_count >= 4 ? "2000" : "1000"
  }
}

resource "google_secret_manager_secret" "db_credentials" {
  for_each = local.dbs

  secret_id = "${each.value.name}-${local.cell_name}-credentials"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_service_networking_connection.private]
}

resource "google_secret_manager_secret_version" "db_credentials" {
  for_each = local.dbs

  secret = google_secret_manager_secret.db_credentials[each.key].id
  secret_data = jsonencode({
    host     = google_alloydb_instance.this[each.key].ip_address
    port     = 5432
    username = each.value.username
    password = random_password.db[each.key].result
    database = each.value.db_name
  })
}
