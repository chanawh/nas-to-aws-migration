# SNS + SQS: the event pipeline.
#   S3 ObjectCreated -> SQS queue -> Lambda validator -> DynamoDB audit
#   verify FAIL (from the CLI) -> SNS topic -> email, if alert_email is set
# The DLQ catches validation messages that fail 3 times, so a poison
# object does not block the queue forever.
resource "aws_sns_topic" "alerts" {
  name = "nas2aws-migration-alerts"
  tags = local.tags
}

resource "aws_sns_topic_subscription" "alert_email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sqs_queue" "validator_dlq" {
  name = "nas2aws-migration-validator-dlq"
  tags = local.tags
}

resource "aws_sqs_queue" "events" {
  name                       = "nas2aws-migration-events"
  visibility_timeout_seconds = 180
  message_retention_seconds  = 1209600 # 14 days

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.validator_dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.tags
}

resource "aws_sqs_queue_policy" "events" {
  queue_url = aws_sqs_queue.events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "s3.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.events.arn
        Condition = {
          ArnLike = { "aws:SourceArn" = "arn:aws:s3:::${var.bucket_prefix}-*" }
        }
      },
    ]
  })
}
