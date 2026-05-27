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

locals {
  azure_lb_service_names = join(" ", concat(
    ["gateway-proxy-internal-lb"],
    var.public_access_enabled ? ["gateway-proxy-public-lb"] : [],
  ))
}

resource "terraform_data" "load_balancer_cleanup" {
  input = {
    internal_lb_ip      = kubernetes_service_v1.internal_lb.status[0].load_balancer[0].ingress[0].ip
    cluster_name        = azurerm_kubernetes_cluster.this.name
    namespace           = kubernetes_namespace_v1.gloo_system.metadata[0].name
    node_resource_group = local.node_resource_group
    private_link_name   = "${local.cell_name}-pls"
    public_ip_id        = azurerm_public_ip.external.id
    public_ip_name      = azurerm_public_ip.external.name
    resource_group      = azurerm_resource_group.this.name
    service_names       = local.azure_lb_service_names
    subscription_id     = var.subscription_id
  }

  depends_on = [
    azurerm_kubernetes_cluster.this,
    azurerm_public_ip.external,
    azurerm_subnet.aks,
    azurerm_subnet.pls,
    kubernetes_ingress_v1.private,
    kubernetes_service_v1.internal_lb,
    kubernetes_service_v1.public_lb,
    pineconebyoc_aks_api_server_waiter.this,
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
  ]

  provisioner "local-exec" {
    when        = destroy
    interpreter = ["/bin/sh", "-c"]
    command     = <<-EOT
      set -eu
      kubeconfig="$(mktemp)"
      trap 'rm -f "$kubeconfig"' EXIT
      az account set --subscription '${self.input.subscription_id}' >/dev/null
      az aks get-credentials \
        --subscription '${self.input.subscription_id}' \
        --resource-group '${self.input.resource_group}' \
        --name '${self.input.cluster_name}' \
        --file "$kubeconfig" \
        --overwrite-existing >/dev/null

      KUBECONFIG="$kubeconfig" kubectl delete service -n '${self.input.namespace}' ${self.input.service_names} --ignore-not-found=true --wait=false

      deadline=$((SECONDS + 900))
      while :; do
        public_ip_association="$(az network public-ip show \
          --resource-group '${self.input.resource_group}' \
          --name '${self.input.public_ip_name}' \
          --query 'ipConfiguration.id' \
          --output tsv 2>/dev/null || true)"

        private_link_exists="0"
        for rg in '${self.input.node_resource_group}' '${self.input.resource_group}'; do
          if az network private-link-service show --resource-group "$rg" --name '${self.input.private_link_name}' >/dev/null 2>&1; then
            private_link_exists="1"
            break
          fi
        done

        public_frontends="$(az network lb list \
          --resource-group '${self.input.node_resource_group}' \
          --query "[].frontendIPConfigurations[?publicIPAddress.id=='${self.input.public_ip_id}'].id" \
          --output tsv 2>/dev/null || true)"

        internal_frontends=""
        if [ -n '${self.input.internal_lb_ip}' ]; then
          internal_frontends="$(az network lb list \
            --resource-group '${self.input.node_resource_group}' \
            --query "[].frontendIPConfigurations[?privateIPAddress=='${self.input.internal_lb_ip}'].id" \
            --output tsv 2>/dev/null || true)"
        fi

        if [ -z "$public_ip_association" ] && [ "$private_link_exists" = "0" ] && [ -z "$public_frontends" ] && [ -z "$internal_frontends" ]; then
          exit 0
        fi
        if [ "$SECONDS" -gt "$deadline" ]; then
          echo "Timed out waiting for Azure load balancer cleanup. public_ip_association=$public_ip_association private_link_exists=$private_link_exists public_frontends=$public_frontends internal_frontends=$internal_frontends" >&2
          exit 1
        fi
        echo "Waiting for Azure load balancer cleanup. public_ip_association=$public_ip_association private_link_exists=$private_link_exists public_frontends=$public_frontends internal_frontends=$internal_frontends"
        sleep 15
      done
    EOT
  }
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
