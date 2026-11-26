# Migration comparison: engine vs `aws s3 sync` vs DataSync

The same dataset, migrated three ways, into per-method buckets
(`<prefix>-<method>-<share>`), with a SHA-256 verify pass per
method. The point is the measured trade-off: when to build a
custom engine, when to use the managed service, when to use the
CLI.

## The three methods

| Method | How it migrates | Verification |
|---|---|---|
| engine | The Python sync engine (`sync_all` + `verify`) | Per-object SHA-256, re-hash actual bytes |
| s3sync | `aws s3 sync` (AWS CLI) | None of its own (compares by size/mtime); the engine's verify pass runs over the s3-sync'd bucket afterwards as the independent check |
| datasync | AWS DataSync, NAS -> S3 (finance share only) | The task's own POINT_IN_TIME_CONSISTENT verification, as reported by the task execution, plus the engine's verify pass over the target bucket |

DataSync covers the finance share only: one task is one
source/destination pair, so a full four-share comparison needs
four tasks. The evidence records the share scope per method, so
the comparison is never apples-to-oranges by accident.

## The harness

`scripts/compare_migration.py --source <dataset> --methods engine,s3sync,datasync --out evidence/comparison-<date>.json`
writes one evidence JSON with, per method: whether it ran (and
the skip reason if not), objects, bytes, seconds, MB/s, and the
verify result. Buckets are emptied before each timed run, so
every run is a full upload.

DataSync is real-AWS-only and is **not emulated** (moto does not
emulate it). On an emulator the harness records it as skipped
with the reason, never a fake number. The DataSync task is in
Terraform (`terraform/datasync_fsx.tf`, gated by
`enable_datasync_s3=false`): an NFS location through a
separately-deployed DataSync agent (a VM near the OneFS node;
only the agent needs to reach the NFS export, `datasync_agent_arn`)
to the finance S3 bucket. Pass the task ARN as
`DATASYNC_TASK_ARN` (or `--datasync-task-arn`, or
`terraform output datasync_s3_task_arn`).

## Measured (Garage, 24-file demo dataset, 2026-11-25)

`evidence/comparison-2026-11-25.json`: engine 1.24 MB/s and
s3sync 0.21 MB/s, both verify 24/24 passed; DataSync skipped
(not emulated). The dataset is 1.16 MB, so these numbers are
per-call overhead, not throughput -- the demo proves both
methods copy correctly and the harness records the timing. The
throughput comparison needs the 10 GB dataset, on real AWS.

## Garage setup note

The Garage key cannot create buckets via the S3 API (it only has
access to buckets pre-created by the CLI), so the eight method
buckets must be created first:
`garage bucket create nas2aws-migration-<method>-<share>` +
`garage bucket allow <bucket> --key bench --read --write --owner`
for each method and share. On real AWS the harness creates the
buckets itself (the OIDC role has `s3:CreateBucket`).

## Run it on real AWS

1. `terraform apply -var enable_datasync_s3=true -var datasync_agent_arn=<arn>` (after the base apply, with the agent deployed).
2. `python scripts/compare_migration.py --source <10 GB dataset> --methods engine,s3sync,datasync --out evidence/comparison-real-aws-<date>.json` with `S3_ENDPOINT=""`.
3. The 10 GB numbers (throughput per method, verify, and the
   DataSync task's reported verification) are the comparison;
   the 24-file demo numbers above are not.
