# CloudWatch: logs for the Lambda validator, and two alarms that
# page (via SNS) instead of failing silently.
#   1. Lambda Errors > 0 in 5 minutes - the validator is broken.
#   2. SQS ApproximateAgeOfOldestMessage > 300s - validation is stuck,
#      messages are piling up unprocessed.
resource "aws_cloudwatch_log_group" "validator" {
  name              = "/aws/lambda/nas2aws-validator"
  retention_in_days = 14
  tags              = local.tags
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "nas2aws-validator-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "The S3 object validator Lambda errored. Check /aws/lambda/nas2aws-validator."
  alarm_actions       = [aws_sns_topic.alerts.arn]
  dimensions          = { FunctionName = aws_lambda_function.validator.function_name }
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "queue_stuck" {
  alarm_name          = "nas2aws-validation-queue-stuck"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 300
  alarm_description   = "Validation queue has a message older than 5 minutes. The validator is not keeping up."
  alarm_actions       = [aws_sns_topic.alerts.arn]
  dimensions          = { QueueName = aws_sqs_queue.events.name }
  tags                = local.tags
}
