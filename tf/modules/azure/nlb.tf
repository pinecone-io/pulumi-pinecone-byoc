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

  depends_on = [
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
  ]
}

resource "kubernetes_service_v1" "internal_lb" {
  wait_for_load_balancer = true

  metadata {
    name      = "gateway-proxy-internal-lb"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    labels = {
      "app.kubernetes.io/managed-by" = "pulumi"
      "app.kubernetes.io/component"  = "internal-load-balancer"
    }
    annotations = {
      "service.beta.kubernetes.io/azure-load-balancer-internal"      = "true"
      "service.beta.kubernetes.io/azure-pls-create"                  = "true"
      "service.beta.kubernetes.io/azure-pls-name"                    = "${local.cell_name}-pls"
      "service.beta.kubernetes.io/azure-pls-visibility"              = "*"
      "service.beta.kubernetes.io/azure-pls-auto-approval"           = var.subscription_id
      "service.beta.kubernetes.io/azure-pls-ip-configuration-subnet" = azurerm_subnet.pls.name
      "service.beta.kubernetes.io/azure-pls-proxy-protocol"          = "false"
    }
  }

  spec {
    type = "LoadBalancer"
    selector = {
      gloo = "gateway-proxy"
    }
    port {
      name        = "https"
      port        = 443
      target_port = 8443
      protocol    = "TCP"
    }
  }

  depends_on = [kubernetes_secret_v1.placeholder_tls]
}

resource "kubernetes_service_v1" "public_lb" {
  count                  = var.public_access_enabled ? 1 : 0
  wait_for_load_balancer = true

  metadata {
    name      = "gateway-proxy-public-lb"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    labels = {
      "app.kubernetes.io/managed-by" = "pulumi"
      "app.kubernetes.io/component"  = "public-load-balancer"
    }
    annotations = {
      "service.beta.kubernetes.io/azure-load-balancer-resource-group" = azurerm_resource_group.this.name
      "service.beta.kubernetes.io/azure-load-balancer-ipv4"           = azurerm_public_ip.external.ip_address
    }
  }

  spec {
    type = "LoadBalancer"
    selector = {
      gloo = "gateway-proxy"
    }
    port {
      name        = "https"
      port        = 443
      target_port = 8443
      protocol    = "TCP"
    }
  }

  depends_on = [kubernetes_secret_v1.placeholder_tls]
}

resource "kubernetes_ingress_v1" "private" {
  metadata {
    name      = "private-gloo-lb"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    labels = {
      "app.kubernetes.io/managed-by" = "pulumi"
      "app.kubernetes.io/component"  = "private-load-balancer"
    }
    annotations = {
      "cert-manager.io/issuer"           = "letsencrypt-prod"
      "kubernetes.io/ingress.allow-http" = "false"
      "pulumi.com/patchForce"            = "true"
      "pulumi.com/skipAwait"             = "true"
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

  depends_on = [kubernetes_secret_v1.placeholder_tls]
}

resource "azurerm_dns_a_record" "private" {
  name                = "private"
  zone_name           = azurerm_dns_zone.this.name
  resource_group_name = azurerm_resource_group.this.name
  ttl                 = 300
  records             = [kubernetes_service_v1.internal_lb.status[0].load_balancer[0].ingress[0].ip]
}

resource "azurerm_dns_cname_record" "private_cnames" {
  for_each = toset(local.dns_cnames)

  name                = "${each.value}.private"
  zone_name           = azurerm_dns_zone.this.name
  resource_group_name = azurerm_resource_group.this.name
  ttl                 = 300
  record              = "private.${local.fqdn}"
}
