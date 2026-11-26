output "bucket_names" {
  description = "One bucket per share."
  value       = local.bucket_names
}

output "bucket_arns" {
  value = { for k, b in aws_s3_bucket.shares : k => b.arn }
}

output "migration_user_name" {
  value = aws_iam_user.migration.name
}

output "kms_key_arn" {
  value = aws_kms_key.migration.arn
}

output "audit_table_name" {
  value = aws_dynamodb_table.audit.name
}

output "alert_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "events_queue_url" {
  value = aws_sqs_queue.events.url
}

output "validator_function_name" {
  value = aws_lambda_function.validator.function_name
}

output "fsx_enabled" {
  description = "True only when enable_fsx_datasync was set. False on every emulator run."
  value       = var.enable_fsx_datasync
}

output "fsx_filesystem_id" {
  value = var.enable_fsx_datasync ? aws_fsx_ontap_file_system.migration[0].id : ""
}

output "datasync_task_arn" {
  value = var.enable_fsx_datasync ? aws_datasync_task.migration[0].arn : ""
}

output "cloudtrail_enabled" {
  value = var.enable_cloudtrail
}

output "efs_enabled" {
  description = "True only when enable_efs was set. False on every emulator run."
  value       = var.enable_efs
}

output "efs_file_system_id" {
  value = var.enable_efs ? aws_efs_file_system.migration[0].id : ""
}

output "efs_dns_name" {
  description = "Mount with: mount -t nfs4 -o nfsvers=4.1 <dns>:/ <mount-point>"
  value       = var.enable_efs ? aws_efs_file_system.migration[0].dns_name : ""
}

output "datasync_s3_task_arn" {
  description = "DataSync NAS -> S3 comparison task. Empty unless enable_datasync_s3 was set."
  value       = var.enable_datasync_s3 ? aws_datasync_task.cmp[0].arn : ""
}

output "crr_enabled" {
  description = "True only when enable_crr was set. False on every emulator run."
  value       = var.enable_crr
}

output "replica_bucket_names" {
  value = var.enable_crr ? { for s in var.shares : s => "${var.bucket_prefix}-${s}-replica" } : {}
}

output "replica_kms_key_arn" {
  value = var.enable_crr ? aws_kms_key.replica[0].arn : ""
}

output "endpoint_url" {
  description = "Emulator address in use, or empty for real AWS."
  value       = var.use_emulator ? var.endpoint_url : ""
}
