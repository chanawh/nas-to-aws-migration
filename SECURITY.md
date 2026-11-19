# Security

## Reporting

This is a lab project. If you find a security issue, open
an issue in the repo. Do not include credentials in the report.

## What is safe here

- No real credentials are committed. Terraform variables
  hold `test` for the emulator only. Real credentials come
  from env vars, never from a file.
- OneFS credentials come from env vars or CLI flags only.
- S3 public access is fully blocked. Buckets are versioned
  and KMS-encrypted.
- IAM is least-privilege: the migration user can only touch
  the four migration buckets, use the KMS key, write to
  the DynamoDB audit table, and publish to SNS.
- CloudTrail, on real AWS, logs every API call with log
  file validation on.

## What is not safe, and is labelled as such

- The OneFS `Everyone` SMB permission in
  `scripts/onefs/setup_onefs.sh` is lab-only. Never use
  it on a production node.
- `skipCertificateValidation` is not set in this project,
  but the OneFS Simulator uses a self-signed cert, and
  the Python bridge skips verification for that lab case
  only. Do not do that on a real cluster.
- The dataset is synthetic. No real user data is in this repo.
- The Lambda validator runs with the permissions in
  `terraform/lambda.tf`, and no more. It can read S3,
  write to DynamoDB, and publish to SNS. It cannot create
  buckets or change IAM.
