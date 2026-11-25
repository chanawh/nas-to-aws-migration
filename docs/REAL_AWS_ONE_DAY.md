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

## GitHub OIDC (no static keys)

The `real-aws` workflow (`.github/workflows/real-aws.yml`)
runs the throughput benchmark and the failure drills against
real S3 without a long-lived access key. Setup, once, in the
AWS account:

1. In IAM, create an OIDC identity provider for
   `token.actions.githubusercontent.com` (audience `sts.amazonaws.com`).
2. Create a role with a trust policy that only allows this
   repo (`repo:chanawh/nas-to-aws-migration:ref:refs/heads/main`
   for dispatch runs, or the specific workflow file) to assume
   it, with a policy limited to the four `nas2aws-migration-*`
   buckets, the KMS key, and CloudWatch Logs.
3. In the repo, set the repository variable `AWS_OIDC_ROLE_ARN`
   to the role ARN (Settings > Secrets and variables > Actions >
   Variables). Never put the role ARN in a file.
4. Dispatch the workflow from the Actions tab (workflow_dispatch).

The workflow is UNTESTED: it has never run against real AWS,
because no account exists in this environment. The first green
run is the evidence; until then it is a script, not a result.
It does not touch `ci.yml`, which stays $0 and green on every
push with no account.

## After the run

Update the resume bullet: replace "in a local AWS emulator
(LocalStack / moto, $0)" with "on AWS (one-day lab run,
under $1, destroyed same day)". Keep the 24-file dataset
honest. The point is not the size of the data, it is that
the Terraform, the sync, and the validator ran against
real AWS APIs, not a pretend one.

## Ceiling measurements on real AWS

The ceiling drills (v1.2.0) were measured on emulators
only: moto accepts puts with a disabled KMS key, neither
emulator throttles, and Object Lock is not enforced anywhere
except real AWS. v1.2.1 makes the same drill scripts measure
genuinely on real AWS. Nothing about the drill CLIs changed,
so the OIDC workflow above is still the primary path.

### What the workflow runs

Dispatch `real-aws` from the Actions tab (same OIDC setup
as above). It runs, in order:

1. `scripts/run_throughput.py --workers 1 4 8 --per-share 100`
   with `S3_ENDPOINT=""` (real S3, 424 files / 401 MB).
   This step also creates the four `nas2aws-migration-*`
   buckets, so the drills that follow have buckets to work
   in.
2. `scripts/drills/run_all.py --runs 3` with `S3_ENDPOINT=""`.
   Evidence lands in `evidence/failure-matrix-real-aws-<date>.json`
   and `evidence/throughput-real-aws-<date>.json`, uploaded as
   the `real-aws-evidence` artifact.

### What becomes genuine on real AWS

| Drill | On emulators | On real AWS |
|---|---|---|
| expired credentials | real (Garage validates keys) | real, same code |
| KMS denied | simulated (stub) | **genuine**: creates a KMS key, disables it, measures the real `KMSInvalidStateException`, recovers with a second enabled key. Both drill keys are scheduled for deletion (7-day window, the KMS minimum) |
| DNS / endpoint | real (client-side) | real, same code |
| throttling | simulated (stub) | **genuine burst**: 32 workers x 200 puts against one prefix, counts real 503 SlowDown responses. `simulated=False` only if at least one genuine 503 was seen; if S3 absorbs the burst, the drill reports `throttled==0` honestly and `run_all.py` exits non-zero for that drill, because no throttling was measured |
| interrupted multipart | real (Garage multipart API) | real, same code |
| silent corruption | real (Garage) | real, same code |
| ransomware | real restore on moto versioning | **genuine**: same attack/detect/restore against the real buckets, and the Object Lock probe genuinely tests enforcement |

### Before dispatching

Apply Terraform first, with Object Lock on:

1. In `terraform/terraform.tfvars`, set `endpoint_url = ""`,
   `use_emulator = false`, `bucket_prefix` to a globally
   unique name, `enable_object_lock = true`, and `alert_email`.
2. `terraform init && terraform apply`. This creates the
   four share buckets with versioning on and the GOVERNANCE
   Object Lock configuration the ransomware probe tests.
   Without `enable_object_lock=true`, the probe records that
   the lock was not enforced, and the run proves nothing
   about Object Lock.
3. The OIDC role needs, beyond the four buckets: `kms:CreateKey`,
   `kms:DisableKey`, `kms:ScheduleKeyDeletion`, and
   `kms:DescribeKey` (the KMS drill creates its own keys, so
   the resource cannot be named in advance — scope it to the
   account), plus `s3:PutBucketVersioning` (the ransomware
   drill enables versioning idempotently).

### What is NOT part of the one-day run

The 1 TB scale run is explicitly not part of this run. At
measured rates it takes ~14–16 hours; scan parallelization
is the next lever before a 1 TB run is worth doing. The
one-day run is the 424-file throughput benchmark plus the
seven drills, under $1 without FSx.

### Azure Blob real run

The Blob drills take the same shape on real Azure. The drill
steps are in `docs/AZURE_BLOB.md` (Reproduce section); the
only additions for a real account are creating the storage
account first (any region, Standard LRS, blob versioning on —
the drills create their own containers) and the three env vars
(set them in the shell before running
`scripts/azure/drills/run_all.py`):

```sh
export AZURE_BLOB_ENDPOINT=https://<account>.blob.core.windows.net
export AZURE_STORAGE_ACCOUNT_NAME=<account>
export AZURE_STORAGE_KEY=<key>
~/workspace/nas-to-aws-migration-venv-azure/bin/python \
    scripts/azure/drills/run_all.py --runs 3 \
    --out evidence/azure-failure-matrix-real-azure-$(date +%F).json
```

With the vars unset, the drills run against Azurite exactly
as before. On real Azure the throttle drill runs the same
32 x 200 burst and counts genuine 503 ServerBusy responses
(429s are counted separately in the detail); soft-delete
restore and immutability, which return 501 on Azurite,
become measurable.
