# CloudTrail: audit every API call in the account for this project.
# REAL AWS ONLY, gated by enable_cloudtrail = false by default.
# Not emulated. When enabled, all S3, IAM, KMS, DynamoDB, Lambda and
# DataSync calls are logged to a dedicated, versioned S3 bucket.
resource "aws_s3_bucket" "cloudtrail" {
  count         = var.enable_cloudtrail ? 1 : 0
  bucket        = "${var.bucket_prefix}-cloudtrail"
  force_destroy = false
  tags          = local.tags
}

resource "aws_s3_bucket_versioning" "cloudtrail" {
  count  = var.enable_cloudtrail ? 1 : 0
  bucket = aws_s3_bucket.cloudtrail[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  count                   = var.enable_cloudtrail ? 1 : 0
  bucket                  = aws_s3_bucket.cloudtrail[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudtrail" "migration" {
  count                         = var.enable_cloudtrail ? 1 : 0
  name                          = "nas2aws-migration"
  s3_bucket_name                = aws_s3_bucket.cloudtrail[0].id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true

  event_selector {
    read_write_type           = "All"
    include_management_events = true
  }

  tags = local.tags
}
