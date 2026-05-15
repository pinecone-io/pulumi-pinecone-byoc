terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.35"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    pineconebyoc = {
      source  = "pinecone.io/internal/pineconebyoc"
      version = "0.1.0"
    }
  }
}

provider "google" {
  project                         = var.project
  region                          = var.region
  add_terraform_attribution_label = false
}

provider "google-beta" {
  project                         = var.project
  region                          = var.region
  add_terraform_attribution_label = false
}

data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://${google_container_cluster.this.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.this.master_auth[0].cluster_ca_certificate)
}

provider "pineconebyoc" {
  api_url          = var.api_url
  pinecone_api_key = var.pinecone_api_key
}
