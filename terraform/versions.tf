terraform {
  required_version = ">= 1.9, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Emulator-first provider. Against real AWS, set endpoint_url = "" and
# provide real credentials; against LocalStack or moto, endpoint_url is
# the emulator address and the test credentials below are ignored by it.
provider "aws" {
  region     = var.region
  access_key = var.aws_access_key
  secret_key = var.aws_secret_key
  # Session token for temporary credentials (GitHub OIDC). Empty by
  # default, which is the emulator case; the real-aws workflow passes
  # the OIDC session token. Without it, temporary credentials fail
  # authentication even when the key and secret are correct.
  token = var.aws_session_token

  skip_credentials_validation = var.use_emulator
  skip_metadata_api_check     = var.use_emulator
  skip_requesting_account_id  = var.use_emulator
  s3_use_path_style           = var.use_emulator

  dynamic "endpoints" {
    for_each = var.use_emulator && var.endpoint_url != "" ? [1] : []
    content {
      s3         = var.endpoint_url
      iam        = var.endpoint_url
      sts        = var.endpoint_url
      cloudwatch = var.endpoint_url
      logs       = var.endpoint_url
      kms        = var.endpoint_url
      dynamodb   = var.endpoint_url
      sns        = var.endpoint_url
      sqs        = var.endpoint_url
      lambda     = var.endpoint_url
      # FSx, DataSync and CloudTrail are real-AWS only (gated off by
      # default), so they are not given emulator endpoints here.
    }
  }
}
