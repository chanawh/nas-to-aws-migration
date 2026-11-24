# Azure Blob target (Azurite emulator)

The Blob side of the migration factory: a Blob mirror of the S3 sync
engine, one container per share, verified live against Azurite 3.38.0
on 2026-11-24. No real Azure exists in this VM; everything below
that says "real-Azure-only" was not run here and is not claimed.

## What was built

| File | What it is |
|---|---|
| `scripts/start_azurite.sh` | Installs Azurite 3.38.0 via npm if missing, starts it on 127.0.0.1:10000 with blob versioning ON, waits until it answers, smoke-tests versioning (upload twice, list 2 versions) and exits non-zero if versioning is off |
| `python/azure_blob/blob_sync.py` | Blob sync engine: scan source, compare by SHA-256 stored in blob metadata, upload only new/changed blobs, `_manifest.json` per container, quota pre-check per share (over-quota share skipped whole), staged-block upload for files >= 8 MiB, read-back verification after every upload |
| `python/azure_blob/blob_verify.py` | Re-downloads every blob, re-hashes, reports missing/extra/mismatched |
| `python/tests/test_blob_sync.py` | 9 tests, live against Azurite |
| `scripts/azure/drills/common.py` | Shared drill harness (same record shape as `scripts/drills/common.py`) |
| `scripts/azure/drills/drill_sas_expired.py` | Expired SAS + not-yet-valid SAS |
| `scripts/azure/drills/drill_key_denied.py` | Wrong account key |
| `scripts/azure/drills/drill_dns.py` | Dead endpoints |
| `scripts/azure/drills/drill_throttle.py` | Simulated 503 retry + live burst |
| `scripts/azure/drills/drill_interrupted.py` | Abandoned staged blocks |
| `scripts/azure/drills/drill_corruption.py` | Silent corruption of 3 blobs |
| `scripts/azure/drills/run_all.py` | Runs all 6 drills 3x, writes the evidence file |

One container per share, same 4 shares and same 24-file dataset as
the S3 side (`nas2azure-migration-finance`, `-it-support`,
`-it-operations`, `-operations`). SHA-256 is stored in blob metadata
only; Blob ETags are never used as a checksum. Azure has no native
per-container quota, so quotas are enforced by the engine from
`quotas.json` before upload, exactly like the S3 engine. The NAS
group mapping is stored in blob metadata (`owner`, `nas-group`) plus
`acl.json` as the audit trail.

## What ran against Azurite (evidence)

Evidence: `evidence/azure-failure-matrix-2026-11-24.json`.

**Tests — 9/9 pass** (5.2 s on a fresh emulator, Azurite 3.38.0, azure-storage-blob 12.31.1):

- initial sync uploads all 24 files, verify passes on all 4 shares
- second sync is a no-op (0 copied, 24 skipped unchanged)
- changed file re-uploaded alone, others skipped
- corrupted blob detected as mismatched and repaired by re-sync
- dry-run uploads nothing and writes no manifest
- over-quota share skipped whole (finance, 6 files, quota 10 bytes)
- manifest stored as `_manifest.json` blob (6 entries for finance)
- 9 MB file uploads as 2 staged 8 MiB blocks and verifies
  (SHA-256 in metadata matches the downloaded bytes)
- delete removes an orphan only with the flag

**Drills — 6/6 detected 3/3 and recovered 3/3** (medians over 3 runs):

| Drill | Median detect | Median recover | Simulated |
|---|---|---|---|
| expired SAS (expired + not-yet-valid, both 403) | 0.011 s | 0.006 s | no |
| key denied (wrong account key, 403) | 0.004 s | 0.007 s | no |
| DNS/endpoint failure (dead endpoints) | 0.004 s | 0.005 s | no |
| throttling (stubbed 503 x2, then success in 3 attempts; live burst of 50 parallel puts: 0 throttled, 0 failed) | 0.300 s | 0.197 s | **yes** |
| interrupted upload (2 uncommitted blocks listed, blob not visible before commit; re-stage + commit, SHA matches) | 0.013 s | 0.356 s | no |
| silent corruption (3 blobs corrupted; verify found exactly those 3; repair re-uploaded 3, final verify passed) | 0.175 s | 0.443 s | no |

## What is real-Azure-only (not run, not claimed)

- Soft-delete restore and immutability / legal hold: Azurite
  returns 501 for both (`APINotImplemented`). No restore path
  exists in the emulator.
- Genuine throttling (429/503): Azurite never throttles. The
  throttling drill's failure is a stub that raises 503 twice;
  only the retry code and the live 50-put burst (0 throttled)
  were measured.
- Terraform apply against Azurite: the `azurerm` provider needs
  real ARM. Containers here are created by the SDK/test harness,
  not Terraform. No Terraform files for Azure were written.
- Entra ID / RBAC auth, stored access policies, lifecycle
  management, geo-redundant recall.

## Traps found while building (so the next run does not re-find them)

- Versioning is CLI-flag-only. `Set Blob Service Properties`
  accepts the versioning flag and silently drops it. The start
  script passes
  `--accountConfigAsJson '{"isBlobVersioningEnabled":true}'` on
  every start and the smoke test fails loudly if a stale data dir
  was started without it.
- The SDK's default retry policy retries a refused connection
  with exponential backoff (up to ~120 s per probe). The DNS
  drill passes `retry_total=0` so a dead endpoint fails fast
  (detect 0.005 s instead of hanging).
- `get_block_list()` defaults to the committed list. An
  interrupted upload's residue is only visible with
  `block_list_type="uncommitted"`.
- The DNS drill probes two closed localhost ports
  (127.0.0.1:9, 127.0.0.1:59999), not a nonexistent hostname:
  this sandbox blocks egress to unresolvable hosts, so that probe
  hangs on an access prompt instead of failing fast. Both prove
  the same thing — the client fails before any Blob call
  completes — and the recovery call against the healthy endpoint
  is identical.
- `get_block_list` returns a `(committed, uncommitted)` tuple in
  azure-storage-blob 12.31.1, not an object with attributes.

## Reproduce

```bash
# 1. Start Azurite with versioning on (installs via npm if missing)
bash scripts/start_azurite.sh

# 2. Tests (9 tests, live against Azurite)
cd python
~/workspace/nas-to-aws-migration-venv-azure/bin/python -m unittest discover -s tests -p "test_blob_sync.py" -v

# 3. Drills (6 drills x 3 runs, writes the evidence file)
cd ..
~/workspace/nas-to-aws-migration-venv-azure/bin/python -u scripts/azure/drills/run_all.py \
    --out evidence/azure-failure-matrix-2026-11-24.json

# Stop Azurite
kill "$(cat /tmp/azurite/azurite.pid)"
```

Venv: `~/workspace/nas-to-aws-migration-venv-azure/` (azure-storage-blob
12.31.1). Azurite install: `~/workspace/azurite/` (3.38.0). Azurite
data: `/tmp/azurite-data` (ephemeral; delete it to reset the emulator).
`/tmp` here is a 512 MB tmpfs: repeated runs with 9 MB test blobs
fill it (it hit 100% once, which killed Azurite mid-run and made
every test fail with "No space left on device"). If a run fails
that way, stop Azurite, delete `/tmp/azurite-data`, and restart.
