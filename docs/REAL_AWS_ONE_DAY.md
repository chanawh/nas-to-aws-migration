# One real AWS run, for under $1

The emulator version is the $0 version that gets you the
interview. One real run is what turns "lab project" into
"ran on AWS". Do it once, for a day, then destroy it.

## Cost, 24-file dataset, one day, us-west-2

| Service | Cost for one day |
|---|---|
| S3, 24 files, 1.1 MB, Standard | under $0.01 |
| KMS, one customer-managed key | $1.00/month, so ~$0.03 for one day |
| DynamoDB, PAY_PER_REQUEST, 24 writes | $0.00 at this volume |
| Lambda, a handful of invocations | $0.00, inside the free tier |
| SQS, SNS, CloudWatch alarms | $0.00 at this volume |
| Data transfer in | $0.00 |
| **Total, without FSx** | **under $0.10** |

With FSx for NetApp ONTAP enabled, add ~$4.10 for one day:
1024 GiB minimum at ~$0.12/GiB/month, so ~$123/month,
~$4.10/day, plus DataSync at $0.0125/GB transferred,
under $0.01 for 1.1 MB. Total with FSx: **about $4.20 for
one day**. Do the FSx run on a separate day, or skip it
until an interviewer asks for it.

## Steps

1. In `terraform/terraform.tfvars`, set `endpoint_url = ""`,
   `use_emulator = false`, `bucket_prefix` to a globally
   unique name, and `alert_email` to your email.
2. `terraform init && terraform apply`
3. `python -m migrate_s3.cli sync --source <your source> --dynamodb-table nas2aws-migration-audit`
4. `python -m migrate_s3.cli verify --source <your source>`
5. `terraform destroy` the same day. Do not leave it running.
   The $1 KMS key is the only thing that bills monthly, and
   destroying the stack deletes it.

## After the run

Update the resume bullet: replace "in a local AWS emulator
(LocalStack / moto, $0)" with "on AWS (one-day lab run,
under $1, destroyed same day)". Keep the 24-file dataset
honest. The point is not the size of the data, it is that
the Terraform, the sync, and the validator ran against
real AWS APIs, not a pretend one.
