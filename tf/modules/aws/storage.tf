resource "aws_s3_bucket" "pinecone" {
  for_each      = toset(["data", "index-backups", "wal", "janitor", "internal"])
  bucket        = substr("pc-${each.key}-${local.cell_name}", 0, 63)
  force_destroy = !var.deletion_protection
  tags          = merge(local.tags, { Name = substr("pc-${each.key}-${local.cell_name}", 0, 63) })
}

resource "aws_s3_bucket_versioning" "pinecone" {
  for_each = aws_s3_bucket.pinecone
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "pinecone" {
  for_each                = aws_s3_bucket.pinecone
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pinecone" {
  for_each = aws_s3_bucket.pinecone
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.kms_key_arn == null ? "AES256" : "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = var.kms_key_arn == null ? null : true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "pinecone" {
  for_each = aws_s3_bucket.pinecone
  bucket   = each.value.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
    expiration {
      expired_object_delete_marker = true
    }
    noncurrent_version_expiration {
      noncurrent_days = 3
    }
  }

  rule {
    id     = "delete-activity-scrapes"
    status = "Enabled"
    filter { prefix = "activity-scrapes/" }
    expiration { days = 30 }
  }

  rule {
    id     = "delete-janitor"
    status = "Enabled"
    filter { prefix = "janitor/" }
    expiration { days = 7 }
  }

  rule {
    id     = "delete-lag-reporter"
    status = "Enabled"
    filter { prefix = "lag-reporter/" }
    expiration { days = 14 }
  }
}

resource "aws_iam_role" "storage_integration" {
  name = "${local.resource_prefix}-storage-integration-${local.resource_suffix}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = {
      Sid       = "AllowAccountRoles"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
      Action    = "sts:AssumeRole"
    }
  })
  tags = merge(local.tags, { Name = "${local.resource_prefix}-storage-integration" })
}

resource "aws_iam_role_policy" "storage_integration" {
  role = aws_iam_role.storage_integration.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:ListBucket", "s3:GetObject"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "node_assume_storage_integration" {
  role = aws_iam_role.node.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = "arn:aws:iam::*:role/*"
    }]
  })
}

