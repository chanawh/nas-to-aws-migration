# Cutover runbook (AWS / S3)

Architecture lives in `README.md` only; this file executes it. S3 is
object storage: cutover means pointing readers at the bucket, not
mounting a share. There is no SMB path to hand users.

## Preconditions

- The emulator is running (LocalStack on :4566 or moto on :5000) or
  real AWS credentials are set.
- `terraform apply` is done and the outputs show the four buckets.
- The source dataset exists.

## Step 1 - Pre-flight

```bash
cd python
python -m migrate_s3.cli sync --source <source> --endpoint-url <endpoint> --dry-run
```

Expected: per-share counts, total uploaded = 24 files, `[dry-run]`
on the last line, nothing uploaded, no manifest object written. If
the ACL pre-flight fails, fix `acl.json` / `users.csv` first.

## Step 2 - Initial sync

```bash
python -m migrate_s3.cli sync --source <source> --endpoint-url <endpoint>
```

Expected: 24 objects uploaded. Record the bytes and duration from
the last line - that is your measured throughput for the production
estimate. Do not proceed on a partial sync.

## Step 3 - Verify (the gate)

```bash
python -m migrate_s3.cli verify --source <source> --endpoint-url <endpoint>
```

Expected: every share PASS, `verify: PASS` on the last line. If any
share FAILs, the printed keys are the exact objects to re-sync.
Re-run step 2; it uploads only the failed objects. Do not cut over
on a FAIL.

## Step 4 - Final delta sync and freeze

1. Freeze writes to the source.
2. Re-run steps 2 and 3. Expected: 0 objects uploaded on the second
   sync, verify PASS.
3. If a file changed between runs, it is uploaded in this delta sync.

## Step 5 - Cutover

1. Point readers at the buckets (`bucket_names` output). On real AWS
   that is a bucket policy / presigned URLs / CloudFront, not a UNC
   path - the access pattern is part of the design review, not an
   afterthought.
2. Spot-check one object per bucket with the verify step.
3. Keep the source NAS read-only for one week (production) or until
   the next drill (lab). Do not delete the source on cutover day.

## Step 6 - Rollback

1. Run `verify` and name the object. If verify passes, the object is
   in S3 and the problem is access or a path, not data loss.
2. If verify fails on that object, re-run `sync` - it re-uploads only
   that object.
3. If a bucket is unreachable, point readers back at the source NAS
   (still read-only, still intact) and treat the cutover as failed.

## Evidence to keep

- The `report` output: `python -m migrate_s3.cli report --source <source> --endpoint-url <endpoint>`
- The verify PASS line, with the date.
