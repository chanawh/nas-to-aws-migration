variable "region" {
  description = "AWS region. Seattle -> us-west-2."
  type        = string
  default     = "us-west-2"
}

variable "use_emulator" {
  description = "True for LocalStack / moto, false for real AWS. When false, endpoint_url is ignored and real credentials are required."
  type        = bool
  default     = true
}

variable "endpoint_url" {
  description = "Emulator address. LocalStack default http://localhost:4566, moto default http://localhost:5000. Empty means real AWS."
  type        = string
  default     = "http://localhost:4566"
}

variable "aws_access_key" {
  description = "Access key. 'test' is fine for an emulator; never commit a real key."
  type        = string
  default     = "test"
  sensitive   = true
}

variable "aws_secret_key" {
  description = "Secret key. 'test' is fine for an emulator; never commit a real key."
  type        = string
  default     = "test"
  sensitive   = true
}

variable "aws_session_token" {
  description = "Session token for temporary credentials (GitHub OIDC / STS assume-role). Empty means no token. The real-aws workflow passes the OIDC session token here; without it, temporary credentials fail. Never commit a real token."
  type        = string
  default     = ""
  sensitive   = true
}

variable "bucket_prefix" {
  description = "Prefix for the four migration buckets. Must be globally unique on real AWS - change it before a real apply."
  type        = string
  default     = "nas2aws-migration"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,60}$", var.bucket_prefix))
    error_message = "Bucket prefix must be lowercase letters, numbers and hyphens only."
  }
}

variable "shares" {
  description = "One S3 bucket per source NAS share. This mirrors the four Azure Files shares in the Azure twin."
  type        = list(string)
  default     = ["finance", "it-support", "it-operations", "operations"]
}

variable "owner" {
  description = "Owner tag value."
  type        = string
  default     = "andrew-chan"
}

variable "glacier_after_days" {
  description = "Days before current objects transition to Glacier. Lab default is short so the rule is visible in a plan; production is 90+."
  type        = number
  default     = 90
}

variable "enable_object_lock" {
  description = "Enable S3 Object Lock (GOVERNANCE, 30-day retention) on the share buckets. This is the ransomware protection for the drill in scripts/drills/ransomware.py: a locked object cannot be deleted or overwritten until retention expires. Default false: Object Lock can only be enabled at bucket creation, so turning it on recreates the buckets, and neither moto nor Garage enforces it (verified 2026-11-24), so lock enforcement is real-AWS-only."
  type        = bool
  default     = false
}

variable "alert_email" {
  description = "Email for SNS verify-failure and alarm alerts. Empty means no email subscription - the topic still exists, and alerts are visible in the console / via the CLI."
  type        = string
  default     = ""
}

variable "enable_fsx_datasync" {
  description = "Real AWS only: build the FSx for NetApp ONTAP file system and the DataSync task from S3 to FSx. False by default - neither service is emulated for free (FSx is not emulated, DataSync is not in the free tiers), so enabling this against an emulator will fail."
  type        = bool
  default     = false
}

variable "enable_cloudtrail" {
  description = "Real AWS only: build a CloudTrail trail logging all API calls to a dedicated S3 bucket. False by default - not emulated."
  type        = bool
  default     = false
}

variable "enable_efs" {
  description = "Real AWS only: build an EFS file system (encrypted) with mount targets in the default VPC, as the secondary migration target for POSIX workloads that cannot go to object storage. False by default - EFS is not emulated, so enabling this against an emulator will fail."
  type        = bool
  default     = false
}

variable "vpc_cidr" {
  description = "VPC CIDR for the FSx/DataSync network. Only used when enable_fsx_datasync is true."
  type        = string
  default     = "10.111.0.0/16"
}

variable "fsx_subnet_cidr" {
  description = "Subnet CIDR for FSx for NetApp ONTAP. Only used when enable_fsx_datasync is true."
  type        = string
  default     = "10.111.1.0/24"
}
