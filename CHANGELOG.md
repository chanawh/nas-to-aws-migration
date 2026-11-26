# Changelog

## v1.3.0, 2026-11-25

Three additions to the optimal design, all real-AWS-only and
gated off by default. EFS secondary target
(`terraform/efs.tf`, `scripts/migrate_efs.py`): an encrypted
EFS file system with mount targets in the default VPC (data
sources, zero new network resources) and a copy script with
per-share quotas and a SHA-256 verify pass reusing the engine's
hashing and manifest shape; the copy/verify logic is unit-tested
against a local stand-in and the backend label comes from
`/proc/mounts`, so a local directory is never labelled EFS.
Migration comparison (`scripts/compare_migration.py`):
the same dataset three ways (engine, `aws s3 sync`, DataSync)
into per-method buckets with a SHA-256 verify pass per method;
DataSync is recorded as skipped on emulators (not emulated),
never a fake number, and covers the finance share only; a new
DataSync NAS -> S3 task (finance) is gated by
`enable_datasync_s3`, via a separately-deployed agent. Verified
live on Garage, 24-file demo dataset
(`evidence/comparison-2026-11-25.json`): engine 1.24 MB/s and
s3sync 0.21 MB/s, both verify 24/24 passed -- the dataset is
1.16 MB, so these are per-call overhead, not throughput; the
10 GB comparison runs on real AWS. Cross-region replication
(`terraform/crr.tf`, `scripts/crr_lag.py`,
`scripts/crr_failover.py`): per-share replication rules to
us-east-1 with a second customer-managed KMS key there, a
canary-based lag measurement, and a failover drill that verifies
the replica against the source manifest; both scripts refuse
cleanly on emulators (exit 2, "not measured"). Replication
itself is real-AWS-only and unmeasured. `scripts/empty_buckets.py`
also empties the replica buckets so the destroy works with CRR
enabled. Tests: 5 EFS + 12 comparison + 12 CRR, all passing
(63 total with the 34 existing). Terraform validates clean.

## v1.2.1, 2026-11-24

Real-cloud measurement, no new emulator numbers. The v1.2.0
drills measured on emulators only and could not measure
genuinely on real cloud as written; v1.2.1 fixes that.
scripts/drills/common.py gains is_real_aws() (S3_ENDPOINT
unset) and backend_label() (returns "real AWS S3 (us-west-2)"
on real AWS, the emulator label otherwise), used by all
seven S3 drills. drill_kms.py: on real AWS it creates a KMS
key, disables it, measures the genuine KMSInvalidStateException,
and recovers with a second enabled key; both keys scheduled
for deletion. drill_throttle.py: on real AWS a 32 x 200 burst
against one prefix counts genuine 503 SlowDown responses;
simulated=False only if a genuine 503 was seen, otherwise
the drill reports no throttling honestly. ransomware.py: on
real AWS it runs against the real buckets (versioning from
Terraform) and the Object Lock probe genuinely tests
enforcement (probe bucket left with one 1-day-retained object).
Azure: scripts/azure/drills/common.py reads
AZURE_BLOB_ENDPOINT / AZURE_STORAGE_ACCOUNT_NAME /
AZURE_STORAGE_KEY, Azurite is the default when unset, BACKEND
is "real Azure Blob" when a real account is configured; all
six Azure drills use the env-aware client, and the throttle
drill has the same real burst mode (genuine 503 ServerBusy).
Fixed start_garage.sh node-ID parsing (awk picked the table
header, so layout assign failed on a fresh checkout).
docs/REAL_AWS_ONE_DAY.md gains a "Ceiling measurements on
real AWS" section: OIDC dispatch as the primary path, what
becomes genuine, Terraform with enable_object_lock=true
first, and the 1 TB run explicitly out of scope. Verified:
S3 drills 7/7 detected+recovered 3/3 on Garage/moto,
Azure drills 6/6 3/3 on Azurite, tests 34/34 (9 Blob
skipped in the S3 venv) + 9/9 Blob tests. Nothing here has
run on real AWS/Azure yet; the first OIDC dispatch is the
evidence.

## v1.2.0, 2026-11-24

Ceiling version, S3 side. Parallel sync engine (`--workers`,
per-thread boto3 clients, multipart upload for files >= 8 MiB,
unchanged SHA-256 verification and quota rules; ADR-0006).
Measured, not hoped for: 1.13x at 4 workers, ~1.0x at 8 on
Garage — the bottleneck is the emulator, see docs/THROUGHPUT.md
and evidence/throughput-2026-11-24.json. Six failure drills +
ransomware drill in scripts/drills/ (3 runs each, all detected
and recovered; KMS denial and genuine throttling simulated,
Object Lock enforcement real-AWS-only), docs/FAILURE_MATRIX.md
and evidence/failure-matrix-2026-11-24.json. Terraform Object
Lock (GOVERNANCE, 30 days) gated behind enable_object_lock,
validated by plan only. docs/COST.md (list-price model +
Cost Explorer capture template, no real spend). GitHub OIDC
workflow for real-AWS runs (.github/workflows/real-aws.yml),
untested against real AWS. Azure Blob side against Azurite:
Blob mirror engine (9/9 tests), 6 Blob drills 3/3 detected
and recovered, docs/AZURE_BLOB.md and
evidence/azure-failure-matrix-2026-11-24.json; soft delete,
immutability, genuine throttling, and Terraform apply are
real-Azure-only. Tests 34/34 (25 S3 + 9 Blob, was 22).

## v1.1.0, 2026-11-22

Scaled evidence: 10,024-object / 10.49 GB run through the
unmodified engine against Garage v1.3.0 (local disk-backed
S3 emulator; moto is in-memory and cannot hold 10 GB in a
small VM). Sync 776.07s, verify PASS zero mismatches, 10/10
corruptions detected, repair exactly 10, final verify PASS.
New: `scripts/run_scaled_demo.py`, `docs/SCALED_RUN.md`,
`evidence/scaled-run-10gb-2026-11-22.json` (incl. S3
list-price cost model, ~$0.30 first month for the dataset).

## v1.0.0, 2026-11-19

Final portfolio form. System design, five ADRs, SECURITY.md,
CONTRIBUTING.md, interview guide, roadmap, CI green on the
final commit, tagged `v1.0.0`.

## Earlier

Initial build, 2026-11-18. Verified live locally, CI added,
honest limits stated up front.
