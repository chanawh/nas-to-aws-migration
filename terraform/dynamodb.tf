# DynamoDB: the audit log. One item per object, per sync.
# PK is the share, SK is the object key, so a share's whole audit is
# one query. The S3 manifest object is the resume point; this table
# is the evidence trail - what was copied, when, with which SHA-256,
# and whether the Lambda validator later agreed.
# PAY_PER_REQUEST: $0 at lab scale, no capacity to size.
resource "aws_dynamodb_table" "audit" {
  name         = "nas2aws-migration-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "share"
  range_key    = "object_key"

  attribute {
    name = "share"
    type = "S"
  }

  attribute {
    name = "object_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = local.tags
}
