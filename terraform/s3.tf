# One bucket per share. A bucket is the AWS analogue of an Azure Files
# share: the unit of access control, versioning, and lifecycle.
# S3 has no SMB/NFS mount - that is the honest difference from the Azure
# twin. Files land as objects, with the SHA-256 in object metadata.
locals {
  tags = {
    project = "nas-to-aws-migration"
    owner   = var.owner
  }
  bucket_names = {
    for share in var.shares : share => "${var.bucket_prefix}-${share}"
  }
}

resource "aws_s3_bucket" "shares" {
  for_each = toset(var.shares)
  bucket   = local.bucket_names[each.value]
  tags     = merge(local.tags, { share = each.value })
  # Lab only: on an emulator, terraform destroy must empty the bucket
  # itself, or destroy fails with BucketNotEmpty after a sync. On real
  # AWS this is false, so a destroy refuses to delete data.
  force_destroy = var.use_emulator
}

resource "aws_s3_bucket_versioning" "shares" {
  for_each = aws_s3_bucket.shares
  bucket   = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "shares" {
  for_each = aws_s3_bucket.shares
  bucket   = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.migration.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "shares" {
  for_each                = aws_s3_bucket.shares
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "shares" {
  for_each = aws_s3_bucket.shares
  bucket   = each.value.id

  rule {
    id     = "to-glacier"
    status = "Enabled"

    filter {
      prefix = ""
    }

    transition {
      days          = var.glacier_after_days
      storage_class = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
