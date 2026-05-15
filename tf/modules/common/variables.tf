variable "cloud" {
  type = string
}

variable "cell_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "is_prod" {
  type = bool
}

variable "domain" {
  type = string
}

variable "region" {
  type = string
}

variable "public_access_enabled" {
  type = bool
}

variable "api_url" {
  type = string
}

variable "registry_type" {
  type = string
}

variable "pinetools_image" {
  type = string
}

variable "pinecone_version" {
  type = string
}

variable "pinetools_dependency_ids" {
  type    = list(string)
  default = []
}

variable "cpgw_api_key" {
  type      = string
  sensitive = true
}

variable "gcps_api_key" {
  type      = string
  sensitive = true
  default   = null
}

variable "datadog_api_key" {
  type      = string
  sensitive = true
  default   = null
}

variable "azure_storage_access_key" {
  type      = string
  sensitive = true
  default   = null
}

variable "storage_integration_credentials" {
  type      = map(string)
  sensitive = true
  default   = {}
}

variable "db_credentials" {
  type = object({
    control = object({
      host          = string
      readonly_host = string
      port          = string
      username      = string
      password      = string
      dbname        = string
    })
    system = object({
      host          = string
      readonly_host = string
      port          = string
      username      = string
      password      = string
      dbname        = string
    })
  })
  sensitive = true
}

variable "pulumi_outputs" {
  type = any
}
