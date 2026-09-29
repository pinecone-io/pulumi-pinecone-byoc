output "cluster_name" { value = local.cell_name }
output "region" { value = var.region }
output "organization_id" { value = pineconebyoc_environment.this.org_id }
output "organization_name" { value = pineconebyoc_environment.this.org_name }
output "vpc_id" { value = aws_vpc.this.id }
output "cluster_endpoint" { value = aws_eks_cluster.this.endpoint }
output "data_bucket" { value = aws_s3_bucket.pinecone["data"].bucket }
output "control_db_endpoint" { value = aws_rds_cluster.db["control"].endpoint }
output "system_db_endpoint" { value = aws_rds_cluster.db["system"].endpoint }
output "certificate_arn" { value = aws_acm_certificate_validation.public.certificate_arn }
output "environment_id" { value = pineconebyoc_environment.this.id }
output "environment_name" { value = pineconebyoc_environment.this.env_name }
output "service_account_id" { value = pineconebyoc_service_account.this.id }
output "service_account_client_id" { value = pineconebyoc_service_account.this.client_id }
output "api_key_project_id" { value = pineconebyoc_project_api_key.sli.project_id }
output "alb_controller_role_arn" { value = aws_iam_role.alb_controller.arn }
output "cluster_autoscaler_role_arn" { value = aws_iam_role.cluster_autoscaler.arn }
output "external_dns_role_arn" { value = aws_iam_role.external_dns.arn }
output "subdomain" { value = pineconebyoc_environment.this.env_name }
output "sli_checkers_project_id" { value = pineconebyoc_project_api_key.sli.project_id }
output "cpgw_api_key" {
  value     = pineconebyoc_cpgw_api_key.this.key
  sensitive = true
}
output "cpgw_admin_api_key_id" { value = pineconebyoc_cpgw_api_key.this.key_id }
output "datadog_api_key_id" { value = pineconebyoc_datadog_api_key.this.key_id }
output "customer_tags" { value = var.tags }
output "pulumi_backend_url" { value = "s3://${aws_s3_bucket.pulumi_state.bucket}?region=${var.region}&awssdk=v2" }
output "pulumi_secrets_provider" { value = "awskms:///${aws_kms_key.pulumi_secrets.arn}?region=${var.region}" }
output "storage_integration_role_arn" { value = aws_iam_role.storage_integration.arn }
output "amp_region" { value = pineconebyoc_amp_access.this.amp_region }
output "amp_remote_write_endpoint" { value = pineconebyoc_amp_access.this.amp_remote_write_endpoint }
output "amp_sigv4_role_arn" { value = pineconebyoc_amp_access.this.pinecone_role_arn }
output "amp_ingest_role_arn" { value = aws_iam_role.amp_ingest.arn }
output "vpc_endpoint_service_name" { value = aws_vpc_endpoint_service.this.service_name }
output "kubeconfig" {
  sensitive = true
  value = yamlencode({
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
}
output "update_kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.this.name}"
}

