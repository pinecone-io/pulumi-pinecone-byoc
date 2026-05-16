locals {
  private_alb_name = substr("${split(".", local.subdomain)[0]}-priv-alb", 0, 32)
  public_alb_name  = substr("${split(".", local.subdomain)[0]}-alb", 0, 32)
  tls_secret_name  = "${split(".", local.subdomain)[0]}-tls"
  alb_tags_string  = join(",", [for k, v in local.tags : "${k}=${v}"])
}

resource "aws_security_group" "nlb" {
  vpc_id      = aws_vpc.this.id
  description = "Security group for ${local.resource_prefix} NLB"
  tags        = merge(local.tags, { Name = "${local.resource_prefix}-nlb-sg" })

  ingress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS from anywhere"
  }

  egress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS to ALB"
  }

  depends_on = [
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
  ]
}

resource "aws_security_group" "private_alb" {
  vpc_id      = aws_vpc.this.id
  description = "Security group for ${local.resource_prefix} private ALB - only from NLB"
  tags        = merge(local.tags, { Name = "${local.resource_prefix}-private-alb-sg" })

  ingress {
    protocol        = "tcp"
    from_port       = 443
    to_port         = 443
    security_groups = [aws_security_group.nlb.id]
    description     = "HTTPS from NLB only"
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound traffic"
  }
}

resource "kubernetes_ingress_v1" "private_gloo_http2" {
  metadata {
    name      = "private-gloo-lb"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    annotations = {
      "kubernetes.io/ingress.class"                              = "alb"
      "cert-manager.io/issuer"                                   = "letsencrypt-prod"
      "alb.ingress.kubernetes.io/group.name"                     = "private-pinecone"
      "alb.ingress.kubernetes.io/load-balancer-name"             = local.private_alb_name
      "alb.ingress.kubernetes.io/scheme"                         = "internal"
      "alb.ingress.kubernetes.io/target-type"                    = "ip"
      "alb.ingress.kubernetes.io/healthcheck-path"               = "/"
      "alb.ingress.kubernetes.io/healthcheck-protocol"           = "HTTPS"
      "alb.ingress.kubernetes.io/backend-protocol"               = "HTTPS"
      "alb.ingress.kubernetes.io/backend-protocol-version"       = "HTTP2"
      "alb.ingress.kubernetes.io/listen-ports"                   = "[{\"HTTPS\": 443}]"
      "alb.ingress.kubernetes.io/certificate-arn"                = aws_acm_certificate_validation.private.certificate_arn
      "alb.ingress.kubernetes.io/conditions.gateway-proxy"       = "[{\"field\":\"http-header\",\"httpHeaderConfig\":{\"httpHeaderName\":\"Content-Type\",\"values\":[\"application/grpc\"]}}]"
      "external-dns.alpha.kubernetes.io/ingress-hostname-source" = "annotation-only"
      "external-dns.alpha.kubernetes.io/hostname"                = "private-ingress.${local.subdomain}.pinecone.io"
      "alb.ingress.kubernetes.io/tags"                           = local.alb_tags_string
      "alb.ingress.kubernetes.io/group.order"                    = "1"
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
              port { number = 443 }
            }
          }
        }
      }
    }
    tls {
      hosts       = local.private_dns_domains
      secret_name = local.tls_secret_name
    }
  }
  depends_on = [
    helm_release.aws_load_balancer_controller,
    aws_acm_certificate_validation.private,
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
  ]
}

resource "kubernetes_ingress_v1" "private_gloo_http1" {
  metadata {
    name      = "private-gloo-lb-http1"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    annotations = {
      "kubernetes.io/ingress.class"                              = "alb"
      "cert-manager.io/issuer"                                   = "letsencrypt-prod"
      "alb.ingress.kubernetes.io/group.name"                     = "private-pinecone"
      "alb.ingress.kubernetes.io/load-balancer-name"             = local.private_alb_name
      "alb.ingress.kubernetes.io/scheme"                         = "internal"
      "alb.ingress.kubernetes.io/target-type"                    = "ip"
      "alb.ingress.kubernetes.io/healthcheck-path"               = "/"
      "alb.ingress.kubernetes.io/healthcheck-protocol"           = "HTTPS"
      "alb.ingress.kubernetes.io/backend-protocol"               = "HTTPS"
      "alb.ingress.kubernetes.io/backend-protocol-version"       = "HTTP1"
      "alb.ingress.kubernetes.io/listen-ports"                   = "[{\"HTTPS\": 443}]"
      "alb.ingress.kubernetes.io/certificate-arn"                = aws_acm_certificate_validation.private.certificate_arn
      "external-dns.alpha.kubernetes.io/ingress-hostname-source" = "annotation-only"
      "alb.ingress.kubernetes.io/tags"                           = local.alb_tags_string
      "alb.ingress.kubernetes.io/group.order"                    = "2"
    }
  }
  spec {
    rule {
      http {
        path {
          path      = "/"
          path_type = "Exact"
          backend {
            service {
              name = "gateway-proxy"
              port { number = 443 }
            }
          }
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
              port { number = 443 }
            }
          }
        }
      }
    }
    tls {
      hosts       = local.private_dns_domains
      secret_name = local.tls_secret_name
    }
  }
  depends_on = [kubernetes_ingress_v1.private_gloo_http2]
}

resource "pineconebyoc_aws_alb_waiter" "private" {
  name   = local.private_alb_name
  region = var.region

  depends_on = [
    kubernetes_ingress_v1.private_gloo_http1,
    module.common,
  ]
}

resource "aws_lb_target_group" "private_alb" {
  name        = substr("${local.cell_name}-tg", 0, 32)
  target_type = "alb"
  port        = 443
  protocol    = "TCP"
  vpc_id      = aws_vpc.this.id
  health_check {
    enabled  = true
    port     = "traffic-port"
    protocol = "HTTPS"
  }
  tags = merge(local.tags, { Name = "${local.cell_name}-alb-tg" })

  depends_on = [
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
  ]
}

resource "aws_lb_target_group_attachment" "private_alb" {
  target_group_arn = aws_lb_target_group.private_alb.arn
  target_id        = pineconebyoc_aws_alb_waiter.private.arn
  port             = 443
}

resource "aws_lb" "nlb" {
  name                             = "${local.resource_prefix}-nlb-${local.resource_suffix}"
  internal                         = true
  load_balancer_type               = "network"
  subnets                          = [for s in aws_subnet.private : s.id]
  security_groups                  = [aws_security_group.nlb.id]
  enable_cross_zone_load_balancing = true
  tags                             = merge(local.tags, { Name = "${local.resource_prefix}-nlb" })

  depends_on = [
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
  ]
}

resource "aws_lb_listener" "nlb" {
  load_balancer_arn = aws_lb.nlb.arn
  port              = 443
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.private_alb.arn
  }
  tags = merge(local.tags, { Name = "${local.resource_prefix}-listener" })
}

resource "aws_vpc_endpoint_service" "this" {
  acceptance_required        = false
  allowed_principals         = ["*"]
  network_load_balancer_arns = [aws_lb.nlb.arn]
  private_dns_name           = "*.private.${local.subdomain}.byoc.pinecone.io"
}

resource "aws_route53_record" "privatelink_dns_verification" {
  zone_id = aws_route53_zone.this.zone_id
  name    = "${aws_vpc_endpoint_service.this.private_dns_name_configuration[0].name}.${local.fqdn}"
  type    = "TXT"
  ttl     = 1800
  records = [aws_vpc_endpoint_service.this.private_dns_name_configuration[0].value]
}

resource "pineconebyoc_aws_vpc_endpoint_dns_verification" "this" {
  service_id   = aws_vpc_endpoint_service.this.id
  service_name = aws_vpc_endpoint_service.this.service_name
  region       = var.region
  depends_on   = [aws_route53_record.privatelink_dns_verification]
}

resource "aws_vpc_endpoint" "internal" {
  service_name        = aws_vpc_endpoint_service.this.service_name
  vpc_endpoint_type   = "Interface"
  vpc_id              = aws_vpc.this.id
  subnet_ids          = [for s in aws_subnet.private : s.id]
  security_group_ids  = [aws_eks_cluster.this.vpc_config[0].cluster_security_group_id]
  private_dns_enabled = true
  depends_on          = [pineconebyoc_aws_vpc_endpoint_dns_verification.this]
  timeouts {
    create = "15m"
  }
}

resource "kubernetes_ingress_v1" "public_gloo_http2" {
  count = var.public_access_enabled ? 1 : 0
  metadata {
    name      = "gloo-lb"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    annotations = {
      "kubernetes.io/ingress.class"                              = "alb"
      "alb.ingress.kubernetes.io/group.name"                     = "pinecone"
      "alb.ingress.kubernetes.io/load-balancer-name"             = local.public_alb_name
      "alb.ingress.kubernetes.io/scheme"                         = "internet-facing"
      "alb.ingress.kubernetes.io/target-type"                    = "ip"
      "alb.ingress.kubernetes.io/healthcheck-path"               = "/"
      "alb.ingress.kubernetes.io/healthcheck-protocol"           = "HTTPS"
      "alb.ingress.kubernetes.io/backend-protocol-version"       = "HTTP2"
      "alb.ingress.kubernetes.io/backend-protocol"               = "HTTPS"
      "alb.ingress.kubernetes.io/listen-ports"                   = "[{\"HTTPS\": 443}]"
      "alb.ingress.kubernetes.io/conditions.gateway-proxy"       = "[{\"field\":\"http-header\",\"httpHeaderConfig\":{\"httpHeaderName\":\"Content-Type\",\"values\":[\"application/grpc\"]}}]"
      "alb.ingress.kubernetes.io/certificate-arn"                = aws_acm_certificate_validation.public.certificate_arn
      "alb.ingress.kubernetes.io/tags"                           = local.alb_tags_string
      "alb.ingress.kubernetes.io/group.order"                    = "1"
      "external-dns.alpha.kubernetes.io/ingress-hostname-source" = "annotation-only"
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
              port { number = 443 }
            }
          }
        }
      }
    }
    tls {
      hosts = [
        "*.svc.${local.fqdn}",
        "ingress.${local.fqdn}",
        "prometheus.${local.fqdn}",
        "metrics.${local.fqdn}",
      ]
      secret_name = local.tls_secret_name
    }
  }
  depends_on = [
    helm_release.aws_load_balancer_controller,
    aws_acm_certificate_validation.public,
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
  ]
}

resource "kubernetes_ingress_v1" "public_gloo_http1" {
  count = var.public_access_enabled ? 1 : 0
  metadata {
    name      = "gloo-lb-http1"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    annotations = {
      "kubernetes.io/ingress.class"                              = "alb"
      "alb.ingress.kubernetes.io/group.name"                     = "pinecone"
      "alb.ingress.kubernetes.io/load-balancer-name"             = local.public_alb_name
      "alb.ingress.kubernetes.io/scheme"                         = "internet-facing"
      "alb.ingress.kubernetes.io/target-type"                    = "ip"
      "alb.ingress.kubernetes.io/healthcheck-path"               = "/"
      "alb.ingress.kubernetes.io/healthcheck-protocol"           = "HTTPS"
      "alb.ingress.kubernetes.io/backend-protocol-version"       = "HTTP1"
      "alb.ingress.kubernetes.io/backend-protocol"               = "HTTPS"
      "alb.ingress.kubernetes.io/listen-ports"                   = "[{\"HTTPS\": 443}]"
      "alb.ingress.kubernetes.io/certificate-arn"                = aws_acm_certificate_validation.public.certificate_arn
      "alb.ingress.kubernetes.io/tags"                           = local.alb_tags_string
      "alb.ingress.kubernetes.io/group.order"                    = "2"
      "external-dns.alpha.kubernetes.io/ingress-hostname-source" = "annotation-only"
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
              port { number = 443 }
            }
          }
        }
      }
    }
    tls {
      hosts = [
        "*.svc.${local.fqdn}",
        "ingress.${local.fqdn}",
        "prometheus.${local.fqdn}",
        "metrics.${local.fqdn}",
      ]
      secret_name = local.tls_secret_name
    }
  }
  depends_on = [kubernetes_ingress_v1.public_gloo_http2]
}

resource "pineconebyoc_aws_alb_waiter" "public" {
  count  = var.public_access_enabled ? 1 : 0
  name   = local.public_alb_name
  region = var.region

  depends_on = [
    kubernetes_ingress_v1.public_gloo_http1,
    module.common,
  ]
}

resource "aws_route53_record" "public_alb_alias" {
  count   = var.public_access_enabled ? 1 : 0
  zone_id = aws_route53_zone.this.zone_id
  name    = "ingress.${local.fqdn}"
  type    = "A"
  alias {
    name                   = pineconebyoc_aws_alb_waiter.public[0].dns_name
    zone_id                = pineconebyoc_aws_alb_waiter.public[0].hosted_zone_id
    evaluate_target_health = true
  }
}
