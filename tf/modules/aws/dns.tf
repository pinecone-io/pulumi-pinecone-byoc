resource "aws_route53_zone" "this" {
  name          = local.fqdn
  force_destroy = true
  tags          = merge(local.tags, { Name = "${local.resource_prefix}-dns-zone" })

  depends_on = [terraform_data.control_plane_ready]
}

resource "pineconebyoc_dns_delegation" "this" {
  subdomain    = local.subdomain
  nameservers  = aws_route53_zone.this.name_servers
  api_url      = var.api_url
  cpgw_api_key = pineconebyoc_cpgw_api_key.this.key
  depends_on   = [aws_route53_zone.this, pineconebyoc_cpgw_api_key.this]
}

locals {
  public_cert_domains = {
    wildcard     = "*.${local.fqdn}"
    apex         = local.fqdn
    svc_wildcard = "*.svc.${local.fqdn}"
  }

  private_dns_domains = [for cname in local.dns_cnames : "${cname}.private.${local.fqdn}"]
  private_cert_domains = {
    svc_wildcard = local.private_dns_domains[0]
    metrics      = local.private_dns_domains[1]
    prometheus   = local.private_dns_domains[2]
  }
}

resource "aws_route53_record" "cname" {
  for_each        = toset(local.dns_cnames)
  zone_id         = aws_route53_zone.this.zone_id
  name            = "${each.value}.${local.fqdn}"
  type            = "CNAME"
  ttl             = 300
  records         = ["ingress.${local.fqdn}"]
  allow_overwrite = true
}

resource "aws_acm_certificate" "public" {
  domain_name = "*.${local.fqdn}"
  subject_alternative_names = [
    local.fqdn,
    "*.svc.${local.fqdn}",
  ]
  validation_method = "DNS"
  tags              = merge(local.tags, { Name = "${local.resource_prefix}-dns-cert" })
  depends_on        = [pineconebyoc_dns_delegation.this]
}

resource "aws_route53_record" "public_cert_validation" {
  for_each = local.public_cert_domains

  zone_id         = aws_route53_zone.this.zone_id
  name            = one([for dvo in aws_acm_certificate.public.domain_validation_options : dvo.resource_record_name if dvo.domain_name == each.value])
  type            = one([for dvo in aws_acm_certificate.public.domain_validation_options : dvo.resource_record_type if dvo.domain_name == each.value])
  records         = [one([for dvo in aws_acm_certificate.public.domain_validation_options : dvo.resource_record_value if dvo.domain_name == each.value])]
  ttl             = 300
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "public" {
  certificate_arn         = aws_acm_certificate.public.arn
  validation_record_fqdns = [for r in aws_route53_record.public_cert_validation : r.fqdn]
}

resource "aws_acm_certificate" "private" {
  domain_name               = local.private_dns_domains[0]
  subject_alternative_names = slice(local.private_dns_domains, 1, length(local.private_dns_domains))
  validation_method         = "DNS"
  tags                      = merge(local.tags, { Name = "${local.resource_prefix}-private-dns-cert" })
  depends_on                = [pineconebyoc_dns_delegation.this]
}

resource "aws_route53_record" "private_cert_validation" {
  for_each = local.private_cert_domains

  zone_id         = aws_route53_zone.this.zone_id
  name            = one([for dvo in aws_acm_certificate.private.domain_validation_options : dvo.resource_record_name if dvo.domain_name == each.value])
  type            = one([for dvo in aws_acm_certificate.private.domain_validation_options : dvo.resource_record_type if dvo.domain_name == each.value])
  records         = [one([for dvo in aws_acm_certificate.private.domain_validation_options : dvo.resource_record_value if dvo.domain_name == each.value])]
  ttl             = 300
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "private" {
  certificate_arn         = aws_acm_certificate.private.arn
  validation_record_fqdns = [for r in aws_route53_record.private_cert_validation : r.fqdn]
}

resource "terraform_data" "dns_bootstrap_ready" {
  input = {
    zone_id                 = aws_route53_zone.this.zone_id
    public_certificate_arn  = aws_acm_certificate_validation.public.certificate_arn
    private_certificate_arn = aws_acm_certificate_validation.private.certificate_arn
  }

  depends_on = [
    aws_acm_certificate_validation.private,
    aws_acm_certificate_validation.public,
    aws_route53_record.cname,
    pineconebyoc_dns_delegation.this,
  ]
}
