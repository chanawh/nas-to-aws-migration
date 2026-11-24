# Cost

No real AWS spend exists for this project: every run so far was
on emulators (moto, Garage) at $0. This page is a list-price
model for planning a real run, plus the capture template for
recording actual spend when a real run happens. Do not quote
any number here as a bill.

## List-price model (us-west-2, S3 Standard, 2026 list prices)

| Dataset | Storage / month | PUTs | GETs (3 verify passes) | First month total |
|---|---|---|---|---|
| Lab dataset, 24 files, ~1.16 MB | ~$0.00 | ~$0.00 | ~$0.00 | ~$0.00 |
| Scaled run, 10.49 GB, 10,024 objects | $0.24 | $0.05 | $0.01 | **~$0.30** |
| 1 TB, ~1,000,000 x 1 MB objects | $23.55 | $5.00 | $0.40 | **~$29** |
| 80 TB production story, S3 Standard | ~$1,840 | — | — | **~$1,840/mo** |
| 80 TB, Glacier Instant Retrieval | ~$320 | — | — | **~$320/mo** |

The 10.49 GB row is from `evidence/scaled-run-10gb-2026-11-22.json`
(10,486,923,264 bytes, 10,024 objects). The 80 TB rows are the
production story from the README, unchanged. KMS, DynamoDB,
Lambda, SNS/SQS, and CloudWatch add cents at lab scale; at
production scale the KMS request cost and the DynamoDB audit
writes are the lines to watch, not storage.

Other real-run costs, list price: EKS control plane $0.10/hr
(~$73/month if left running, ~$2.40/day); a one-day EKS run is
about $4.50 including 2x t3.medium nodes (see the CSI repo's
one-day runbook). FSx for NetApp ONTAP is ~$0.03/GB-month
provisioned (1024 GiB minimum) and is real-AWS-only.

## Cost Explorer capture (fill in after a real run)

| Item | Value |
|---|---|
| Run date | |
| AWS account / role | |
| Cost Explorer period | |
| Total spend (USD) | |
| S3 storage | |
| S3 requests (PUT/GET) | |
| KMS requests | |
| DynamoDB | |
| Lambda | |
| Data transfer out | |
| Screenshot | `evidence/cost-explorer-<date>.png` |
| Model vs actual | model $___ vs actual $___ (___% difference) |

Take the screenshot from Cost Explorer grouped by service for
the run's date range, and save it under `evidence/` with the
date in the filename. That screenshot is the evidence; the
table above is the record of it.
