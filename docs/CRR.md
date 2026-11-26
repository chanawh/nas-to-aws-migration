# Cross-region replication (CRR)

Each share bucket replicates to a destination bucket in a second
region (`us-east-1` by default, `replica_region`), with a replica
KMS key there. The lag is measured with a canary object; the
failover is verified against the source manifest.

## Design

- Terraform (`terraform/crr.tf`, gated by `enable_crr=false`,
  real-AWS-only): one replication rule per share bucket, a
  replica KMS key in the replica region (via a provider alias),
  destination buckets with versioning on and SSE-KMS with the
  replica key, and the IAM replication role (standard S3/KMS
  replication permissions).
- KMS choice: a **second customer-managed key** in the replica
  region. Replication of KMS-encrypted source objects requires a
  key in the destination region; SSE-S3 for the replica would be
  simpler but would drop the customer-managed-key story on the
  replica side. The rule sets the destination encryption
  configuration to the replica key and enables replication of
  SSE-KMS objects.
- Delete markers replicate (the default). A delete in the primary
  therefore appears as a delete marker in the replica too -- the
  replica mirrors deletes, it does not protect against them. The
  protection against a ransomware delete is Object Lock +
  versioning in the primary bucket; the replica's pre-attack
  versions are still there, behind the delete marker, and the
  failover drill verifies the versions, not the markers. The
  replica buckets carry no Object Lock of their own.
- The replica buckets are `force_destroy=false`.
  `scripts/empty_buckets.py` empties them before a destroy, so
  the one-day run still destroys cleanly.

## Lag measurement

`scripts/crr_lag.py --bucket <share bucket> --replica-bucket <replica bucket> --out evidence/crr-lag-<date>.json`
PUTs a canary object to the source bucket, polls the replica
bucket with `head_object` until it appears (default timeout 900s,
poll every 5s), and records the lag: replica-visible time minus
PUT time. The canary is deleted from the source afterwards (the
delete replicates as a delete marker; that is expected).

## Failover drill

`scripts/crr_failover.py --bucket <share bucket> --replica-bucket <replica bucket> --out evidence/crr-failover-<date>.json`
verifies the replica's objects against the source bucket's
manifest (SHA-256 re-hash of the actual bytes, the same
verification the engine uses) and records the failover steps:
the replica bucket becomes the read source, and a real failover
re-points the application to it. There is no application here,
so the steps are recorded, not executed -- the evidence is the
verification result plus the step list, not a claim that an
application was failed over. A key whose current version is a
delete marker reads as missing (a read returns the marker, not
the version); restoring it is a version-level restore.

## What is verified, and what is not

- `terraform validate` clean; the polling/timing logic and the
  replica verification are unit-tested (`python/tests/test_crr.py`,
  12 tests) with fake time and moto.
- Both scripts refuse cleanly on an emulator (exit 2, evidence
  `status: "not_measured"`, reason recorded). They also record
  "not measured" when replication is not configured, when the
  canary never appears within the timeout, when the source bucket
  has no manifest, or when the replica bucket does not exist.
- Replication itself is real-AWS-only and **not measured**:
  Garage and moto do not replicate, so no lag or failover number
  exists yet.

## Run it on real AWS

1. `terraform apply -var enable_crr=true` (after the base apply,
   with `enable_object_lock=true` if the ransomware story is in
   scope -- the manifest must exist, so run the sync first).
2. `python scripts/crr_lag.py --bucket nas2aws-migration-finance --replica-bucket nas2aws-migration-finance-replica --out evidence/crr-lag-<date>.json` with `S3_ENDPOINT=""`.
3. `python scripts/crr_failover.py --bucket nas2aws-migration-finance --replica-bucket nas2aws-migration-finance-replica --out evidence/crr-failover-<date>.json` with `S3_ENDPOINT=""`.
4. The lag and the failover verification from those two evidence
   files are the CRR numbers. Destroy the same day
   (`scripts/empty_buckets.py` empties the replica buckets first).
