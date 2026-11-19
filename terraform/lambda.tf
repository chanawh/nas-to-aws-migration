# Lambda validator: the second pair of eyes. Every S3 ObjectCreated
# event goes to SQS, SQS invokes this function, and it re-downloads
# the object, re-hashes it, compares it to the Metadata SHA-256, and
# writes the verdict to the DynamoDB audit table. A mismatch is also
# published to the SNS alerts topic.
# Code lives in python/lambda/validator.py, zipped by archive_file.
data "archive_file" "validator" {
  type        = "zip"
  source_file = "${path.module}/../python/lambda/validator.py"
  output_path = "${path.module}/.validator.zip"
}

resource "aws_iam_role" "validator" {
  name = "nas2aws-validator"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "validator" {
  name = "nas2aws-validator"
  role = aws_iam_role.validator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:HeadObject"]
        Resource = [for b in aws_s3_bucket.shares : "${b.arn}/*"]
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
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.validator.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.events.arn
      },
    ]
  })
}

resource "aws_lambda_function" "validator" {
  function_name    = "nas2aws-validator"
  role             = aws_iam_role.validator.arn
  handler          = "validator.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.validator.output_path
  source_code_hash = data.archive_file.validator.output_base64sha256
  timeout          = 60
  memory_size      = 128

  environment {
    variables = {
      AUDIT_TABLE     = aws_dynamodb_table.audit.name
      ALERT_TOPIC_ARN = aws_sns_topic.alerts.arn
      ENDPOINT_URL    = var.use_emulator ? var.endpoint_url : ""
    }
  }

  tags = local.tags
}

resource "aws_lambda_event_source_mapping" "validator" {
  event_source_arn = aws_sqs_queue.events.arn
  function_name    = aws_lambda_function.validator.arn
  batch_size       = 10
}

# S3 -> SQS: every object creation in every share bucket notifies the queue.
resource "aws_s3_bucket_notification" "shares" {
  for_each = aws_s3_bucket.shares
  bucket   = each.value.id

  queue {
    queue_arn     = aws_sqs_queue.events.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = ""
    filter_suffix = ""
  }

  depends_on = [aws_sqs_queue_policy.events]
}
