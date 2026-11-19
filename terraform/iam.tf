# Least-privilege migration identity: S3 object operations on the four
# migration buckets, KMS use for those objects, DynamoDB audit writes,
# and SNS publish on verify failure. No bucket creation, no IAM, no
# other services. On real AWS this is the user the sync engine runs
# as. On an emulator it is still created, so the Terraform apply
# exercises IAM for real.
resource "aws_iam_user" "migration" {
  name = "nas2aws-migration"
  tags = local.tags
}

resource "aws_iam_policy" "migration_s3" {
  name = "nas2aws-migration-s3"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketVersioning",
          "s3:GetBucketLocation",
        ]
        Resource = [for b in aws_s3_bucket.shares : b.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [for b in aws_s3_bucket.shares : "${b.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.migration.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.audit.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.alerts.arn
      },
    ]
  })
}

resource "aws_iam_user_policy_attachment" "migration_s3" {
  user       = aws_iam_user.migration.name
  policy_arn = aws_iam_policy.migration_s3.arn
}
