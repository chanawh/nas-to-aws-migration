# Cross-region replication (CRR): each share bucket replicates to a
# destination bucket in a second region, with a replica KMS key
# there. The lag is measured by scripts/crr_lag.py (PUT a canary,
# poll the replica until it appears) and the failover by
# scripts/crr_failover.py (verify the replica's objects against the
# manifest, then record the failover steps).
# REAL AWS ONLY, gated by enable_crr = false by default. Replication
# is not emulated (Garage and moto do not replicate), so enabling
# this against an emulator will fail at apply time, by design.
#
# KMS choice: a SECOND customer-managed key in the replica region.
# S3 replication of KMS-encrypted source objects requires a key in
# the destination region; SSE-S3 for the replica would be simpler
# but would drop the customer-managed-key story on the replica side.
# The replication rule therefore sets the destination encryption
# configuration to the replica key, and the source selection
# criteria enables replication of SSE-KMS objects.
#
# Delete markers replicate (the default). A delete in the primary
# therefore appears as a delete marker in the replica too -- the
# replica mirrors deletes, it does not protect against them. The
# protection against a ransomware delete is Object Lock + versioning
# in the primary bucket; the replica's pre-attack versions are
# still there, behind the delete marker, and the failover drill
# verifies the versions, not the markers. The replica buckets carry
# no Object Lock of their own.
#
# The replica buckets are force_destroy=false (real AWS, do not
# delete data on destroy). scripts/empty_buckets.py empties them
# before a destroy, so the one-day run still destroys cleanly.
provider "aws" {
  alias      = "replica"
  region     = var.replica_region
  access_key = var.aws_access_key
  secret_key = var.aws_secret_key
  token      = var.aws_session_token

  skip_credentials_validation = var.use_emulator
  skip_metadata_api_check     = var.use_emulator
  skip_requesting_account_id  = var.use_emulator
  s3_use_path_style           = var.use_emulator
}

resource "aws_kms_key" "replica" {
  count                   = var.enable_crr ? 1 : 0
  provider                = aws.replica
  description             = "NAS-to-AWS migration: replica-region KMS key for CRR"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  tags                    = local.tags
}

resource "aws_kms_alias" "replica" {
  count         = var.enable_crr ? 1 : 0
  provider      = aws.replica
  name          = "alias/nas2aws-migration-replica"
  target_key_id = aws_kms_key.replica[0].key_id
}

resource "aws_s3_bucket" "replica" {
  for_each      = var.enable_crr ? toset(var.shares) : toset([])
  provider      = aws.replica
  bucket        = "${var.bucket_prefix}-${each.value}-replica"
  force_destroy = false
  tags          = merge(local.tags, { share = each.value, replica = "true" })
}

resource "aws_s3_bucket_versioning" "replica" {
  for_each = aws_s3_bucket.replica
  provider = aws.replica
  bucket   = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "replica" {
  for_each = aws_s3_bucket.replica
  provider = aws.replica
  bucket   = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.replica[0].arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "replica" {
  for_each                = aws_s3_bucket.replica
  provider                = aws.replica
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# The replication role: S3 assumes it to copy objects from the
# primary buckets to the replica buckets (and to use both KMS keys).
resource "aws_iam_role" "replication" {
  count = var.enable_crr ? 1 : 0
  name  = "nas2aws-s3-replication"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "replication" {
  count = var.enable_crr ? 1 : 0
  name  = "nas2aws-s3-replication"
  role  = aws_iam_role.replication[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
        Resource = [for b in aws_s3_bucket.shares : b.arn]
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObjectVersionForReplication",
        "s3:GetObjectVersionAcl", "s3:GetObjectVersionTagging"]
        Resource = [for b in aws_s3_bucket.shares : "${b.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
        Resource = [for b in aws_s3_bucket.replica : "${b.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.migration.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt"]
        Resource = [aws_kms_key.replica[0].arn]
      },
    ]
  })
}

resource "aws_s3_bucket_replication_configuration" "shares" {
  for_each = var.enable_crr ? aws_s3_bucket.shares : {}
  bucket   = each.value.id
  role     = aws_iam_role.replication[0].arn

  rule {
    id     = "to-${var.replica_region}"
    status = "Enabled"

    destination {
      bucket        = aws_s3_bucket.replica[each.key].arn
      storage_class = "STANDARD"
      encryption_configuration {
        replica_kms_key_id = aws_kms_key.replica[0].arn
      }
    }

    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }
  }

  depends_on = [aws_s3_bucket_versioning.shares]
}
