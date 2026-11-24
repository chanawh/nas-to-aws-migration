# Failure matrix: six failure classes on S3 (2026-11-24)

Every row is a drill in `scripts/drills/`, run 3 times, median
reported. Evidence: `evidence/failure-matrix-2026-11-24.json`.
"Detect" is the time to notice the failure; "recover" is the
time to get back to a verified-good state. The ransomware drill
is separate (`scripts/drills/ransomware.py`) and is in the
matrix because it is the same shape: attack, detect, restore.

## Results

| Failure | Backend | Detect (median) | Recover (median) | Outcome |
|---|---|---|---|---|
| Expired credentials | Garage | 0.009s | 0.017s | 403 AccessDenied; valid creds recover |
| KMS denied | moto + stub | 0.001s | 0.000s | see note below |
| DNS / endpoint failure | client-side | 0.088s | 0.014s | no response from dead endpoint; healthy endpoint recovers |
| Throttling (503 SlowDown) | Garage + stub | 0.303s | 0.282s | see note below |
| Interrupted multipart upload | Garage | 0.011s | 0.264s | orphan listed, object invisible until completed; resumed upload SHA-256 matches |
| Silent corruption | Garage | 0.598s | 1.431s | verify found exactly the 3 corrupted; repair re-uploaded 3, final verify PASS |
| Ransomware (overwrite + delete) | moto (versioning) | 0.063s | 0.226s | 10 overwritten + 10 deleted of 24; all 20 restored, 0 data loss |

## What "simulated" means

Two rows are honest about their limits:

- **KMS denied.** moto accepted a PutObject with a disabled
  KMS key, a pending-deletion key, and an unknown key ID (all
  verified 2026-11-24); Garage has no KMS at all. The
  detect/recover path was exercised against a stubbed client
  that raises the real AWS error (`KMSInvalidStateException`),
  then succeeds on retry with an enabled key. Genuine KMS
  denial is real-AWS-only.
- **Throttling.** A burst of 50 parallel puts against Garage
  returned zero 503s, so the emulators do not throttle. The
  retry code (`put_with_retries`, exponential backoff on
  SlowDown) was exercised against a stubbed client that
  returned SlowDown twice and then succeeded in 3 attempts.
  Genuine throttling is real-AWS-only.

## What the emulators cannot do

- **Versioning:** Garage returns NotImplemented for
  PutBucketVersioning (verified 2026-11-24), so the ransomware
  restore runs on moto, where versioning works.
- **Object Lock:** moto accepts the configuration but does not
  enforce it (a delete of a GOVERNANCE-retained object was
  allowed); Garage returns NotImplemented. The Terraform
  config (`enable_object_lock`, GOVERNANCE, 30 days) is
  validated by `terraform plan` only; enforcement is
  real-AWS-only.

## Reproduce

```sh
# Start Garage (downloads the binary if missing) and load creds:
bash scripts/start_garage.sh
source ~/workspace/nas2aws-ceiling/garage/credentials.env
python scripts/drills/run_all.py --runs 3 \
    --out evidence/failure-matrix-<date>.json
```

Each drill is also standalone: `python scripts/drills/drill_*.py`
prints one JSON record. The ransomware drill runs on moto with
no emulator: `python scripts/drills/ransomware.py`.
