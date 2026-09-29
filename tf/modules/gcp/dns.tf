resource "google_compute_global_address" "external_ip" {
  name       = "externalip-${local.cell_name}"
  project    = var.project
  depends_on = [terraform_data.control_plane_ready]
}

resource "google_dns_managed_zone" "this" {
  name        = "dns-zone-${local.cell_name}"
  dns_name    = "${local.fqdn}."
  description = "DNS zone for ${local.cell_name}"
  depends_on  = [terraform_data.control_plane_ready]
}

resource "google_dns_record_set" "ingress" {
  managed_zone = google_dns_managed_zone.this.name
  name         = "ingress.${local.fqdn}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.external_ip.address]
}

resource "google_dns_record_set" "public_cnames" {
  for_each = toset(local.dns_cnames)

  managed_zone = google_dns_managed_zone.this.name
  name         = "${each.value}.${local.fqdn}."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["ingress.${local.fqdn}."]
}

resource "pineconebyoc_dns_delegation" "this" {
  subdomain    = local.subdomain
  nameservers  = google_dns_managed_zone.this.name_servers
  api_url      = var.api_url
  cpgw_api_key = pineconebyoc_cpgw_api_key.this.key
  depends_on   = [google_dns_managed_zone.this, pineconebyoc_cpgw_api_key.this]
}

resource "terraform_data" "dns_bootstrap_ready" {
  input = {
    external_ip   = google_compute_global_address.external_ip.id
    ingress       = google_dns_record_set.ingress.id
    public_cnames = [for record in google_dns_record_set.public_cnames : record.id]
    delegation    = pineconebyoc_dns_delegation.this.id
  }

  depends_on = [
    google_compute_global_address.external_ip,
    google_dns_managed_zone.this,
    google_dns_record_set.ingress,
    google_dns_record_set.public_cnames,
    pineconebyoc_dns_delegation.this,
  ]
}
