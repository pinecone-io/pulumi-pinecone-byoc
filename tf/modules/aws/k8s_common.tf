module "common" {
  source = "../common"

  cloud                 = "aws"
  cell_name             = local.cell_name
  environment           = var.global_env
  is_prod               = var.global_env == "prod"
  domain                = pineconebyoc_environment.this.env_name
  region                = var.region
  public_access_enabled = var.public_access_enabled
  api_url               = var.api_url
  registry_type         = "ecr"
  pinetools_image       = local.pinetools_image
  pinecone_version      = var.pinecone_version
  cpgw_api_key          = pineconebyoc_cpgw_api_key.this.key
  gcps_api_key          = pineconebyoc_project_api_key.sli.value
  datadog_api_key       = pineconebyoc_datadog_api_key.this.api_key

  db_credentials = {
    control = {
      host          = aws_rds_cluster.db["control"].endpoint
      readonly_host = aws_rds_cluster.db["control"].reader_endpoint
      port          = tostring(aws_rds_cluster.db["control"].port)
      username      = local.dbs.control.username
      password      = random_password.db["control"].result
      dbname        = local.dbs.control.db_name
    }
    system = {
      host          = aws_rds_cluster.db["system"].endpoint
      readonly_host = aws_rds_cluster.db["system"].reader_endpoint
      port          = tostring(aws_rds_cluster.db["system"].port)
      username      = local.dbs.system.username
      password      = random_password.db["system"].result
      dbname        = local.dbs.system.db_name
    }
  }

  pulumi_outputs = merge({
    cell_name                        = local.cell_name
    org_name                         = pineconebyoc_environment.this.org_name
    cloud                            = "aws"
    region                           = var.region
    global_env                       = var.global_env
    subdomain                        = pineconebyoc_environment.this.env_name
    availability_zones               = var.availability_zones
    certificate_arn                  = aws_acm_certificate_validation.public.certificate_arn
    dns_zone_id                      = aws_route53_zone.this.zone_id
    private_endpoint_certificate_arn = aws_acm_certificate_validation.public.certificate_arn
    aws_k8s_version                  = var.kubernetes_version
    aws_ec2_iam_role_arn             = aws_iam_role.node.arn
    aws_subnet_ids                   = [for s in aws_subnet.private : s.id]
    image_registry                   = local.registry_base
    gcp_project                      = local.helmfile_gcp_project
    sli_checkers_project_id          = pineconebyoc_project_api_key.sli.project_id
    aws_storage_integration_role_arn = aws_iam_role.storage_integration.arn
    customer_tags                    = var.tags
    public_access_enabled            = var.public_access_enabled
    external_dns_role_arn            = aws_iam_role.external_dns.arn
    pulumi_backend_url               = "s3://${aws_s3_bucket.pulumi_state.bucket}?region=${var.region}&awssdk=v2"
    pulumi_secrets_provider          = "awskms:///${aws_kms_key.pulumi_secrets.arn}?region=${var.region}"
    pulumi_operator_role_arn         = aws_iam_role.pulumi_operator.arn
    aws_amp_region                   = pineconebyoc_amp_access.this.amp_region
    aws_amp_remote_write_url         = pineconebyoc_amp_access.this.amp_remote_write_endpoint
    aws_amp_sigv4_role_arn           = pineconebyoc_amp_access.this.pinecone_role_arn
    aws_amp_ingest_role_arn          = aws_iam_role.amp_ingest.arn
    }, var.custom_ami_id == null ? {} : {
    base64_encoded_user_data = local.node_user_data
    custom_ami_id            = var.custom_ami_id
  })

  depends_on = [
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
    aws_eks_node_group.this,
    aws_rds_cluster_instance.db,
    aws_s3_bucket_lifecycle_configuration.pinecone,
  ]
}

resource "terraform_data" "cluster_uninstaller_dependencies" {
  input = module.common.cluster_uninstaller_dependency_ids
}

resource "terraform_data" "cluster_uninstaller_cloud_dependencies" {
  input = {
    vpc_id = aws_vpc.this.id
    public_subnet_ids = {
      for az, subnet in aws_subnet.public : az => subnet.id
    }
    private_subnet_ids = {
      for az, subnet in aws_subnet.private : az => subnet.id
    }
    nat_gateway_ids = {
      for az, nat in aws_nat_gateway.this : az => nat.id
    }
    s3_vpc_endpoint_id = aws_vpc_endpoint.s3.id
  }

  depends_on = [
    aws_eip.nat,
    aws_internet_gateway.this,
    aws_nat_gateway.this,
    aws_route.private,
    aws_route.public,
    aws_route_table.private,
    aws_route_table.public,
    aws_route_table_association.private,
    aws_route_table_association.public,
    aws_subnet.private,
    aws_subnet.public,
    aws_vpc.this,
    aws_vpc_endpoint.s3,
  ]
}

resource "pineconebyoc_cluster_uninstaller" "this" {
  kubeconfig = yamlencode({
    apiVersion      = "v1"
    kind            = "Config"
    current-context = aws_eks_cluster.this.name
    clusters = [{
      name = aws_eks_cluster.this.name
      cluster = {
        server                     = aws_eks_cluster.this.endpoint
        certificate-authority-data = aws_eks_cluster.this.certificate_authority[0].data
      }
    }]
    contexts = [{
      name = aws_eks_cluster.this.name
      context = {
        cluster = aws_eks_cluster.this.name
        user    = aws_eks_cluster.this.name
      }
    }]
    users = [{
      name = aws_eks_cluster.this.name
      user = {
        exec = {
          apiVersion = "client.authentication.k8s.io/v1beta1"
          command    = "aws"
          args       = ["eks", "get-token", "--cluster-name", aws_eks_cluster.this.name, "--region", var.region]
        }
      }
    }]
  })
  pinetools_image = local.pinetools_image
  cloud           = "aws"

  depends_on = [
    terraform_data.cluster_uninstaller_cloud_dependencies,
    terraform_data.cluster_uninstaller_dependencies,
    terraform_data.cloud_support_ready,
    terraform_data.dns_bootstrap_ready,
    aws_acm_certificate_validation.private,
    aws_acm_certificate_validation.public,
    aws_eks_addon.ebs_csi,
    aws_eks_addon.kube_proxy,
    aws_eks_addon.vpc_cni,
    aws_eks_cluster.this,
    aws_eks_node_group.this,
    aws_iam_role_policy.alb_controller,
    aws_iam_role_policy.amp_assume_pinecone,
    aws_iam_role_policy.azrebalance,
    aws_iam_role_policy.cluster_autoscaler,
    aws_iam_role_policy.external_dns,
    aws_iam_role_policy.node_allow_pulumi_kms,
    aws_iam_role_policy_attachment.pulumi_operator_eks,
    aws_iam_role_policy_attachment.pulumi_operator_kms,
    aws_iam_role_policy_attachment.pulumi_operator_s3,
    aws_iam_role_policy.storage_integration,
    aws_kms_key.pulumi_secrets,
    aws_lb.nlb,
    aws_lb_listener.nlb,
    aws_lb_target_group.private_alb,
    aws_rds_cluster_instance.db,
    aws_route53_record.cname,
    kubernetes_ingress_v1.private_gloo_http1,
    kubernetes_ingress_v1.private_gloo_http2,
    kubernetes_ingress_v1.public_gloo_http1,
    kubernetes_ingress_v1.public_gloo_http2,
    pineconebyoc_amp_access.this,
    pineconebyoc_aws_alb_waiter.private,
    pineconebyoc_aws_alb_waiter.public,
    aws_vpc_endpoint.internal,
    aws_vpc_endpoint_service.this,
    aws_route53_record.public_alb_alias,
    aws_s3_bucket.pinecone,
    aws_s3_bucket.pulumi_state,
  ]
}
