resource "aws_s3_bucket" "pulumi_state" {
  bucket        = "pc-pulumi-state-${local.cell_name}"
  force_destroy = true
  tags          = merge(local.tags, { Name = "pc-pulumi-state-${local.cell_name}" })
}

resource "aws_s3_bucket_public_access_block" "pulumi_state" {
  bucket                  = aws_s3_bucket.pulumi_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "pulumi_state" {
  bucket = aws_s3_bucket.pulumi_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pulumi_state" {
  bucket = aws_s3_bucket.pulumi_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "pulumi_state" {
  bucket = aws_s3_bucket.pulumi_state.id
  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    abort_incomplete_multipart_upload { days_after_initiation = 2 }
  }
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}

resource "aws_kms_key" "pulumi_secrets" {
  description         = "KMS key for Pulumi secrets encryption - ${local.cell_name}"
  enable_key_rotation = true
  tags                = merge(local.tags, { Name = "${local.resource_prefix}-pulumi-secrets" })
}

resource "aws_kms_alias" "pulumi_secrets" {
  name          = "alias/${local.resource_prefix}-pulumi-secrets-${local.resource_suffix}"
  target_key_id = aws_kms_key.pulumi_secrets.id
}

resource "aws_iam_role" "pulumi_operator" {
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_url_noscheme}:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "${local.oidc_url_noscheme}:sub" = "system:serviceaccount:pulumi-kubernetes-operator:*"
        }
      }
    }]
  })
  tags = merge(local.tags, { Name = "${local.resource_prefix}-pulumi-operator" })
}

resource "aws_iam_role_policy" "pulumi_operator" {
  role = aws_iam_role.pulumi_operator.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.pulumi_state.arn, "${aws_s3_bucket.pulumi_state.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = aws_kms_key.pulumi_secrets.arn
      },
      {
        Effect = "Allow"
        Action = [
          "eks:Describe*",
          "eks:CreateNodegroup",
          "eks:DeleteNodegroup",
          "eks:ListNodegroups",
          "eks:UpdateNodegroupConfig",
          "eks:UpdateNodegroupVersion",
          "eks:TagResource",
          "eks:UntagResource",
          "ec2:Describe*",
          "ec2:CreateLaunchTemplate",
          "ec2:CreateLaunchTemplateVersion",
          "ec2:ModifyLaunchTemplate",
          "ec2:DeleteLaunchTemplate",
          "ec2:RunInstances",
          "ec2:CreateTags",
          "ec2:DeleteTags",
          "autoscaling:CreateOrUpdateTags",
          "autoscaling:DescribeTags",
          "autoscaling:DeleteTags",
          "iam:PassRole",
          "iam:GetRole",
          "iam:ListAttachedRolePolicies"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "node_allow_pulumi_kms" {
  role = aws_iam_role.node.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
      Resource = aws_kms_key.pulumi_secrets.arn
    }]
  })
}

resource "aws_iam_role_policy" "node_allow_customer_kms" {
  count = var.kms_key_arn == null ? 0 : 1
  role  = aws_iam_role.node.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
      Resource = var.kms_key_arn
    }]
  })
}

