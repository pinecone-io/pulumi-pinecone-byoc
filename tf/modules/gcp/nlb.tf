locals {
  tls_secret_name = "${split(".", local.fqdn)[0]}-tls"

  backend_config_manifest = yamlencode({
    apiVersion = "cloud.google.com/v1"
    kind       = "BackendConfig"
    metadata = {
      name      = "edge-nlb-backendconfig"
      namespace = "gloo-system"
      labels = {
        "app.kubernetes.io/managed-by" = "Helm"
      }
      annotations = {
        "meta.helm.sh/release-name"      = "netstack"
        "meta.helm.sh/release-namespace" = "gloo-system"
      }
    }
    spec = {
      timeoutSec = 2147483647
      logging = {
        enable     = true
        sampleRate = 1
      }
      healthCheck = {
        checkIntervalSec   = 5
        timeoutSec         = 1
        healthyThreshold   = 1
        unhealthyThreshold = 3
        port               = 8443
        type               = "HTTP2"
        requestPath        = "/"
      }
      connectionDraining = {
        drainingTimeoutSec = 60
      }
    }
  })

  frontend_config_manifest = yamlencode({
    apiVersion = "networking.gke.io/v1beta1"
    kind       = "FrontendConfig"
    metadata = {
      name      = "ssl-policy-config-${split(".", local.fqdn)[0]}"
      namespace = "gloo-system"
    }
    spec = {
      sslPolicy = try(google_compute_ssl_policy.public[0].name, "")
    }
  })
}

resource "kubernetes_secret_v1" "placeholder_tls" {
  metadata {
    name      = local.tls_secret_name
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
  }

  type = "kubernetes.io/tls"

  data = {
    "tls.crt" = ""
    "tls.key" = ""
  }

  lifecycle {
    ignore_changes = [data, metadata[0].annotations, metadata[0].labels]
  }
}

resource "terraform_data" "backend_config" {
  input = {
    manifest_b64   = base64encode(local.backend_config_manifest)
    kubeconfig_b64 = base64encode(local.kubeconfig)
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      export PATH="/opt/homebrew/share/google-cloud-sdk/bin:/usr/local/share/google-cloud-sdk/bin:/opt/google-cloud-sdk/bin:$HOME/google-cloud-sdk/bin:$PATH"
      KUBECONFIG_FILE="$(mktemp)"
      MANIFEST_FILE="$(mktemp)"
      trap 'rm -f "$KUBECONFIG_FILE" "$MANIFEST_FILE"' EXIT
      printf '%s' "$KUBECONFIG_B64" | base64 -d > "$KUBECONFIG_FILE"
      printf '%s' "$MANIFEST_B64" | base64 -d > "$MANIFEST_FILE"
      kubectl --kubeconfig "$KUBECONFIG_FILE" apply -f "$MANIFEST_FILE"
    EOT
    environment = {
      KUBECONFIG_B64 = self.input.kubeconfig_b64
      MANIFEST_B64   = self.input.manifest_b64
    }
  }

  depends_on = [
    kubernetes_namespace_v1.gloo_system,
    terraform_data.cloud_support_ready,
  ]
}

resource "google_compute_ssl_policy" "public" {
  count           = var.public_access_enabled ? 1 : 0
  name            = "ssl-policy-${local.cell_name}"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"

  depends_on = [
    kubernetes_namespace_v1.gloo_system,
    terraform_data.cloud_support_ready,
  ]
}

resource "terraform_data" "frontend_config" {
  count = var.public_access_enabled ? 1 : 0

  input = {
    manifest_b64   = base64encode(local.frontend_config_manifest)
    kubeconfig_b64 = base64encode(local.kubeconfig)
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      export PATH="/opt/homebrew/share/google-cloud-sdk/bin:/usr/local/share/google-cloud-sdk/bin:/opt/google-cloud-sdk/bin:$HOME/google-cloud-sdk/bin:$PATH"
      KUBECONFIG_FILE="$(mktemp)"
      MANIFEST_FILE="$(mktemp)"
      trap 'rm -f "$KUBECONFIG_FILE" "$MANIFEST_FILE"' EXIT
      printf '%s' "$KUBECONFIG_B64" | base64 -d > "$KUBECONFIG_FILE"
      printf '%s' "$MANIFEST_B64" | base64 -d > "$MANIFEST_FILE"
      kubectl --kubeconfig "$KUBECONFIG_FILE" apply -f "$MANIFEST_FILE"
    EOT
    environment = {
      KUBECONFIG_B64 = self.input.kubeconfig_b64
      MANIFEST_B64   = self.input.manifest_b64
    }
  }

  depends_on = [
    google_compute_ssl_policy.public,
    kubernetes_namespace_v1.gloo_system,
    terraform_data.cloud_support_ready,
  ]
}

resource "pineconebyoc_gcp_forwarding_rule_delete_waiter" "private" {
  project   = var.project
  region    = var.region
  cell_name = local.cell_name

  depends_on = [google_compute_subnetwork.proxy]
}

resource "kubernetes_ingress_v1" "public" {
  count                  = var.public_access_enabled ? 1 : 0
  wait_for_load_balancer = false

  metadata {
    name      = "gloo-lb"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    annotations = {
      "cert-manager.io/issuer"                      = "letsencrypt-prod"
      "kubernetes.io/ingress.allow-http"            = "false"
      "networking.gke.io/v1beta1.FrontendConfig"    = "ssl-policy-config-${split(".", local.fqdn)[0]}"
      "kubernetes.io/ingress.global-static-ip-name" = google_compute_global_address.external_ip.name
    }
  }

  spec {
    rule {
      host = "*.pinecone.io"
      http {
        path {
          path      = "/"
          path_type = "Prefix"
          backend {
            service {
              name = "gateway-proxy"
              port {
                number = 443
              }
            }
          }
        }
      }
    }
    tls {
      hosts = [
        "*.${local.fqdn}",
        "*.svc.${local.fqdn}",
        "*.private.${local.fqdn}",
        "*.svc.private.${local.fqdn}",
      ]
      secret_name = local.tls_secret_name
    }
  }

  depends_on = [
    kubernetes_secret_v1.placeholder_tls,
    terraform_data.frontend_config,
    terraform_data.cloud_support_ready,
  ]
}

resource "kubernetes_ingress_v1" "private" {
  wait_for_load_balancer = false

  metadata {
    name      = "private-gloo-lb"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    labels = {
      "app.kubernetes.io/managed-by" = "pulumi"
      "app.kubernetes.io/component"  = "private-load-balancer"
    }
    annotations = {
      "kubernetes.io/ingress.class"      = "gce-internal"
      "kubernetes.io/ingress.allow-http" = "false"
      "cert-manager.io/issuer"           = "letsencrypt-prod"
      "pulumi.com/patchForce"            = "true"
    }
  }

  spec {
    default_backend {
      service {
        name = "gateway-proxy"
        port {
          number = 443
        }
      }
    }

    rule {
      host = "*.pinecone.io"
      http {
        path {
          path      = "/"
          path_type = "Prefix"
          backend {
            service {
              name = "gateway-proxy"
              port {
                number = 443
              }
            }
          }
        }
      }
    }

    tls {
      hosts = [
        "*.${local.fqdn}",
        "*.svc.${local.fqdn}",
        "*.private.${local.fqdn}",
        "*.svc.private.${local.fqdn}",
      ]
      secret_name = local.tls_secret_name
    }
  }

  depends_on = [
    terraform_data.backend_config,
    kubernetes_secret_v1.placeholder_tls,
    pineconebyoc_gcp_forwarding_rule_delete_waiter.private,
    terraform_data.cloud_support_ready,
  ]
}

resource "pineconebyoc_gcp_forwarding_rule_waiter" "private" {
  project    = var.project
  region     = var.region
  cell_name  = local.cell_name
  depends_on = [kubernetes_ingress_v1.private]
}

resource "google_compute_service_attachment" "this" {
  name                  = "${local.resource_prefix}-psc-${local.cell_name}"
  project               = var.project
  region                = var.region
  description           = "Pinecone service attachment"
  connection_preference = "ACCEPT_AUTOMATIC"
  enable_proxy_protocol = false
  nat_subnets           = [google_compute_subnetwork.psc.id]
  target_service        = pineconebyoc_gcp_forwarding_rule_waiter.private.self_link

  depends_on = [terraform_data.cloud_support_ready]
}

resource "google_dns_record_set" "private_ingress" {
  managed_zone = google_dns_managed_zone.this.name
  name         = "private.${local.fqdn}."
  type         = "A"
  ttl          = 300
  rrdatas      = [pineconebyoc_gcp_forwarding_rule_waiter.private.ip_address]

  depends_on = [terraform_data.cloud_support_ready]
}

resource "google_dns_record_set" "private_cnames" {
  for_each = toset(local.dns_cnames)

  managed_zone = google_dns_managed_zone.this.name
  name         = "${each.value}.private.${local.fqdn}."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["private.${local.fqdn}."]

  depends_on = [terraform_data.cloud_support_ready]
}
