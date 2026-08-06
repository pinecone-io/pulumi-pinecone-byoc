variable "pinecone_api_key" {
  type      = string
  sensitive = true
}

variable "pinecone_version" {
  type = string
}

variable "project" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "availability_zones" {
  type    = list(string)
  default = ["us-central1-a", "us-central1-b"]
}

variable "vpc_cidr" {
  type    = string
  default = "10.112.0.0/12"
}

variable "kubernetes_version" {
  type    = string
  default = "1.33"
}

variable "node_pools" {
  type = list(object({
    name         = string
    machine_type = optional(string, "n2-standard-4")
    min_size     = optional(number, 1)
    max_size     = optional(number, 10)
    disk_size_gb = optional(number, 100)
    labels       = optional(map(string), {})
    taints = optional(list(object({
      key    = string
      value  = string
      effect = optional(string, "NO_SCHEDULE")
    })), [])
  }))
  default = [{
    name = "default"
  }]
}

variable "parent_dns_zone_name" {
  type    = string
  default = "byoc.pinecone.io"
}

variable "public_access_enabled" {
  type    = bool
  default = true
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "api_url" {
  type    = string
  default = "https://api.pinecone.io"
}

variable "global_env" {
  type    = string
  default = "prod"
}

variable "auth0_domain" {
  type    = string
  default = "https://login.pinecone.io"
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "writer_k8s_service_accounts" {
  type = list(string)
  default = [
    "pc-admin/admin-sa",
    "pc-admission-control/admission-control-sa",
    "pc-backup-worker/backup-worker-sa",
    "pc-control-plane/control-planes-sa",
    "pc-data-importer/data-importer-pitboss-sa",
    "pc-data-importer/data-importer-sa",
    "pc-docs-api/docs-api-sa",
    "pc-heartbeat/heartbeat-sa",
    "pc-index-builder-slab/index-builders-sa",
    "pc-index-builder-slab/index-builders-slab-sa",
    "pc-janitor/janitor-sa",
    "pc-query-executors-slab/query-executors-slab-prov-sa",
    "pc-query-executors-slab/query-executors-slab-sa",
    "pc-query-executors-slab/query-executors-slab-shared-sa",
    "pc-query-routers/query-routers-sa",
    "pc-request-log-writers/request-log-writers-sa",
    "pc-shard-manager/shard-manager-sa",
    "prometheus/metrics-proxy-sa",
    "tooling/tooling-sa",
  ]
}

variable "reader_k8s_service_accounts" {
  type    = list(string)
  default = ["gloo-system/netstack-sa"]
}
