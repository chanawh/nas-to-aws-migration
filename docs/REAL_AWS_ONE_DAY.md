# One real AWS run, for under $1 (10 GB default)

The emulator version is the $0 version that gets you the
interview. One real run is what turns "lab project" into
"ran on AWS". Do it once, for a day, then destroy it. The
`real-aws` workflow does the whole thing in one dispatch:
Terraform apply, the throughput benchmark, the failure drills,
then Terraform destroy — all in one job, all in one day. No
emulator is involved: `S3_ENDPOINT` stays `""` throughout.

## Cost, 10 GB default, one dispatch, us-west-2

| Item | Cost |
|---|---|
| S3, 10 GB, Standard, one day | ~$0.01 |
| KMS, one customer-managed key, one day | ~$0.03 |
| KMS drill keys (2, 7-day deletion window) | ~$0.47 |
| S3 PUTs (~31,000) + GETs (~40,000) | ~$0.18 |
| Data transfer out (~40 GB read back) | $0.00 in the Free Tier (100 GB/month); ~$3.60 if exhausted |
| DynamoDB, Lambda, SQS, SNS, CloudWatch | $0.00 at this volume |
| **Total, within the Free Tier** | **under $1** |

With FSx for NetApp ONTAP enabled (`enable_fsx`), add ~$4.20
for one day: 1024 GiB minimum at ~$0.12/GiB/month, so
~$123/month, ~$4.10/day, plus DataSync at $0.0125/GB
transferred, ~$0.13 for 10 GB. Total with FSx: **about $5.20
for one day** within the Free Tier. The 50 GB option
(`per_share` 12500) reads back ~200 GB, past the 100 GB free
tier, so it costs **~$10**, mostly data transfer out — use it
only if you want the 50 GB real-S3 number.

## Setup, once

Run the OIDC setup script with your AWS account ID. It creates
the OIDC identity provider for `token.actions.githubusercontent.com`
(if it does not exist), creates the IAM role
`nas2aws-migration-github-oidc` scoped to this repo, and attaches
the inline policy the workflow needs. It uses your own AWS CLI
credentials; it creates no credentials and stores none. Safe to
re-run.

```sh
bash scripts/setup_oidc.sh 123456789012
```

It prints the role ARN. Set the repo variable `AWS_OIDC_ROLE_ARN`
to that ARN (Settings > Secrets and variables > Actions >
Variables). Never put the role ARN in a file.

### What the role can do

The inline policy (`nas2aws-migration-github-oidc`, in
`scripts/setup_oidc.sh`) covers Terraform apply/destroy for the
stack plus the drill permissions. Resource ARNs are scoped to
the project's names where the service allows it; actions that
cannot be scoped (key creation, list/describe calls) use `*`.

- **S3**: full bucket and object operations on
  `nas2aws-migration-*` (the four share buckets, the CloudTrail
  bucket, the lock-probe bucket), including versioning,
  encryption, lifecycle, notification, Object Lock config, and
  `s3:BypassGovernanceRetention` (the break-glass the cleanup
  and the drills use); `s3:ListAllMyBuckets` on `*`.
- **KMS**: create/describe/disable/schedule-deletion for keys
  (`kms:CreateKey` on `*`, the rest on `key/*` and the
  `alias/nas2aws-migration` alias), plus encrypt/decrypt/
  generate-data-key. The KMS drill creates its own keys, so the
  resource cannot be named in advance — scope is the account.
- **DynamoDB**: the `nas2aws-migration-audit` table
  (create/delete/describe, PutItem/GetItem/DeleteItem/Scan/Query).
- **Lambda**: the `nas2aws-validator` function (create/delete/
  update, event source mappings, invoke).
- **SNS**: the `nas2aws-migration-alerts` topic (create/delete/
  publish/subscribe).
- **SQS**: the `nas2aws-migration-*` queues (create/delete/
  send/receive, queue policy).
- **IAM**: the `nas2aws-migration` user, the `nas2aws-validator`
  and `nas2aws-datasync-s3` roles, and the
  `nas2aws-migration-s3` policy (create/delete, attach/detach,
  `iam:PassRole` for Lambda and DataSync).
- **CloudWatch**: the `/aws/lambda/nas2aws-validator` log group
  and the `nas2aws-*` alarms.
- **FSx / DataSync / CloudTrail / EC2**: the FSx file system,
  SVM and volume, the DataSync locations and task, the
  `nas2aws-migration` trail, and the VPC/subnet/security group
  the FSx network needs. Only used when `enable_fsx` is true.
- **STS**: `sts:GetCallerIdentity` (the Terraform provider uses
  it).

### Manual setup (fallback)

If you would rather do it in the console, the steps are the
same ones the script automates:

1. In IAM, create an OIDC identity provider for
   `token.actions.githubusercontent.com` (audience `sts.amazonaws.com`).
2. Create a role with a trust policy that only allows this
   repo (`repo:chanawh/nas-to-aws-migration:*`, or tighten it to
   `repo:chanawh/nas-to-aws-migration:ref:refs/heads/main` for
   dispatch runs) to assume it, with the policy above
   (the full JSON is in `scripts/setup_oidc.sh`). Set the role's
   maximum session duration to 12 hours — the workflow asks for
   a 4-hour session, which must fit inside it.
3. In the repo, set the repository variable `AWS_OIDC_ROLE_ARN`
   to the role ARN (Settings > Secrets and variables > Actions >
   Variables). Never put the role ARN in a file.

## Dispatch

Actions > `real-aws` > Run workflow. Inputs:

- **per_share** (default `2500`): scale files per share.
  2500 = 10 GB (10,024 objects). `12500` = 50 GB (50,024
  objects, ~$10, see Cost above).
- **workers** (default `1 4 8`): worker counts for the
  throughput benchmark, one timed run each.
- **enable_fsx** (default `false`): when true, builds the FSx
  for NetApp ONTAP file system, the DataSync task, and the
  CloudTrail trail as well (real AWS only, ~$4.20/day).

The workflow (`.github/workflows/real-aws.yml`) runs, in one
job, in order: Terraform apply (real AWS, Object Lock on) →
throughput benchmark → failure drills → empty the buckets →
Terraform destroy → upload the evidence artifact. The exact
steps are in the workflow file; this document does not restate
them. Evidence lands in `evidence/throughput-real-aws-<date>.json`
and `evidence/failure-matrix-real-aws-<date>.json`, uploaded as
the `real-aws-evidence` artifact.

The workflow is UNTESTED: it has never run against real AWS,
because no account exists in this environment. The first green
run is the evidence; until then it is a script, not a result.
It does not touch `ci.yml`, which stays $0 and green on every
push with no account.

## If the run is orphaned

Terraform state is LOCAL to the runner's workspace (no remote
backend). The destroy step has `if: always()`, so a drill
failure does not orphan the stack — but if the runner itself
dies before the destroy completes, the state file is lost and
the infrastructure is orphaned. The only resources that keep
billing are the KMS key ($1/month) and, if `enable_fsx` was
on, the FSx file system (~$123/month). Everything else bills
~$0 when idle (empty S3 buckets, DynamoDB on-demand with no
traffic, Lambda with no invocations, SNS/SQS with no messages).
So the manual cleanup prioritises those two, then the buckets:

```sh
# 1. Empty the buckets (all versions + delete markers), so they
#    can be deleted. Uses your AWS credentials.
python scripts/empty_buckets.py --bucket-prefix nas2aws-migration

# 2. Delete the FSx file system first if enable_fsx was on
#    (find the ID in the console: FSx > File systems).
aws fsx delete-file-system --file-system-id fs-xxxxxxxxxxxxxxxxx

# 3. Schedule the KMS key for deletion (find the key ID in the
#    console: KMS > Customer managed keys).
aws kms schedule-key-deletion --key-id <key-id> --pending-window-in-days 7

# 4. Delete the buckets (share buckets + cloudtrail bucket).
aws s3api delete-bucket --bucket nas2aws-migration-finance
aws s3api delete-bucket --bucket nas2aws-migration-it-support
aws s3api delete-bucket --bucket nas2aws-migration-it-operations
aws s3api delete-bucket --bucket nas2aws-migration-operations
aws s3api delete-bucket --bucket nas2aws-migration-cloudtrail
# The ransomware drill's lock-probe bucket
# (nas2aws-migration-lock-probe) holds one object under a 1-day
# GOVERNANCE retention and cannot be deleted until that expires.
```

The rest (DynamoDB table, Lambda function, SNS topic, SQS
queues, IAM user/roles, CloudWatch alarms) bills ~$0 idle and
can be deleted from the console at leisure — everything is
tagged `project=nas2aws-migration`, so filter by that tag.

The cheapest guard is the destroy step itself: it runs even
when the drills fail. The manual path above is only for a dead
runner.

## After the run

Update the resume bullet: replace "in a local AWS emulator
(LocalStack / moto, $0)" with "on AWS (one-day lab run,
under $1, destroyed same day)". Keep the dataset size honest
(10 GB default, 50 GB if you dispatched that option). The
point is not the size of the data, it is that the Terraform,
the sync, and the validator ran against real AWS APIs, not a
pretend one.

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

1. Terraform apply with `enable_object_lock=true`. This
   creates the four share buckets with versioning on and the
   GOVERNANCE Object Lock configuration the ransomware probe
   tests. Without `enable_object_lock=true`, the probe records
   that the lock was not enforced, and the run proves nothing
   about Object Lock.
2. `scripts/run_throughput.py --workers 1 4 8 --per-share 2500`
   with `S3_ENDPOINT=""` (real S3, 10,024 objects / 10 GB).
3. `scripts/drills/run_all.py --runs 3` with `S3_ENDPOINT=""`.
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

### Two real-AWS code notes

The OIDC credentials are temporary (access key + secret +
session token). The boto3 clients in `scripts/run_throughput.py`
and `scripts/drills/common.py` pass the session token from
`AWS_SESSION_TOKEN`; without it every call fails. The
Terraform provider takes the same token through the
`aws_session_token` variable, which the workflow passes from
the OIDC-assumed credentials.

The share buckets carry a GOVERNANCE Object Lock default
retention (30 days), so deleting a retained object needs the
break-glass `s3:BypassGovernanceRetention`. The cleanup paths
pass it on real AWS only: `empty_bucket` in
`scripts/run_throughput.py` and `scripts/drills/common.py`,
`scripts/empty_buckets.py` (the pre-destroy step), and the
ransomware drill's attack deletes and recovery delete-marker
removal. The ransomware drill's Object Lock probe deliberately
does NOT pass it — the probe's job is to prove a plain delete
is rejected. The attack uses the operator's bypass so the drill
can exercise versioning recovery; the probe is the enforcement
test.

### What is NOT part of the one-day run

The 1 TB scale run is explicitly not part of this run. At
measured rates it takes ~14–16 hours; scan parallelization
is the next lever before a 1 TB run is worth doing. The
one-day run is the 10 GB throughput benchmark plus the
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
