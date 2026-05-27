variable "pinecone_api_key" {
  type      = string
  sensitive = true
}

variable "pinecone_version" {
  type = string
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "availability_zones" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b"]
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
    name          = string
    instance_type = optional(string, "r6in.large")
    min_size      = optional(number, 1)
    max_size      = optional(number, 10)
    desired_size  = optional(number, 3)
    disk_size_gb  = optional(number, 100)
    labels        = optional(map(string), {})
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

variable "custom_ami_id" {
  type    = string
  default = null
}

variable "kms_key_arn" {
  type    = string
  default = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
