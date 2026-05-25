variable "pinecone_api_key" {
  type      = string
  sensitive = true
}

variable "pinecone_version" {
  type = string
}

variable "subscription_id" {
  type = string
}

variable "region" {
  type    = string
  default = "eastus"
}

variable "availability_zones" {
  type    = list(string)
  default = ["1", "2"]
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "kubernetes_version" {
  type    = string
  default = "1.33"
}

variable "node_pools" {
  type = list(object({
    name         = string
    vm_size      = optional(string, "Standard_D4s_v5")
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
  default = [{ name = "default" }]
}

variable "parent_dns_zone_name" {
  type    = string
  default = "byoc.pinecone.io"
}

variable "public_access_enabled" {
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

variable "amp_aws_account_id" {
  type    = string
  default = "713131977538"
}

variable "gcp_project" {
  type    = string
  default = "production-pinecone"
}

variable "tags" {
  type    = map(string)
  default = {}
}
