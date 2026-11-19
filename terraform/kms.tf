# KMS: the S3 buckets now encrypt with a customer-managed key instead
# of the AWS-managed AES256 key. The migration user can use the key
# for encrypt/decrypt, nothing else can. Key rotation is on.
resource "aws_kms_key" "migration" {
  description             = "NAS-to-AWS migration: S3 object encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  tags                    = local.tags
}

resource "aws_kms_alias" "migration" {
  name          = "alias/nas2aws-migration"
  target_key_id = aws_kms_key.migration.key_id
}
