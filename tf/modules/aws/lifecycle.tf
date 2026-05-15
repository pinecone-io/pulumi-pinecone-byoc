resource "terraform_data" "cloud_support_ready" {
  input = {
    cluster_id = aws_eks_cluster.this.id
    node_group_ids = {
      for name, pool in aws_eks_node_group.this : name => pool.id
    }
    db_instance_ids = {
      for name, instance in aws_rds_cluster_instance.db : name => instance.id
    }
    storage_bucket_ids = {
      for name, bucket in aws_s3_bucket.pinecone : name => bucket.id
    }
    pulumi_state_bucket = aws_s3_bucket.pulumi_state.id
  }

  depends_on = [
    aws_eks_addon.ebs_csi,
    aws_eks_node_group.this,
    aws_iam_role_policy.alb_controller,
    aws_iam_role_policy.amp_assume_pinecone,
    aws_iam_role_policy.azrebalance,
    aws_iam_role_policy.cluster_autoscaler,
    aws_iam_role_policy.external_dns,
    aws_iam_role_policy.node_allow_customer_kms,
    aws_iam_role_policy.node_allow_pulumi_kms,
    aws_iam_role_policy.node_assume_storage_integration,
    aws_iam_role_policy.pulumi_operator,
    aws_iam_role_policy.storage_integration,
    aws_kms_alias.pulumi_secrets,
    aws_kms_key.pulumi_secrets,
    aws_rds_cluster_instance.db,
    aws_s3_bucket_lifecycle_configuration.pinecone,
    aws_s3_bucket_lifecycle_configuration.pulumi_state,
    aws_s3_bucket_public_access_block.pinecone,
    aws_s3_bucket_public_access_block.pulumi_state,
    aws_s3_bucket_server_side_encryption_configuration.pinecone,
    aws_s3_bucket_server_side_encryption_configuration.pulumi_state,
    aws_s3_bucket_versioning.pinecone,
    aws_s3_bucket_versioning.pulumi_state,
    aws_secretsmanager_secret_version.db_connection,
    aws_secretsmanager_secret_version.db_master,
    helm_release.aws_load_balancer_controller,
    helm_release.cluster_autoscaler,
    kubernetes_service_account_v1.external_dns,
    pineconebyoc_amp_access.this,
  ]
}
