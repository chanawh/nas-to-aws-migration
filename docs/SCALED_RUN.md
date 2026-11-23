# Scaled run: 10 GB, 10,024 objects (2026-11-22)

The base demo migrates 24 files. This run proves the same
engine at scale: the 24-file dataset plus 2,500 generated
1 MB files per share, 10,024 objects, 10,486,923,264 bytes
(10.49 GB), across the 4 shares.

## Backend choice

moto keeps objects in RAM, so a 10 GB dataset does not fit a
small VM. This run used **Garage v1.3.0**, a local disk-backed
S3-compatible server, driven by the unmodified sync engine
through `scripts/run_scaled_demo.py` (endpoint and credentials
via environment). MinIO's community binaries are discontinued;
SeaweedFS was tried first and abandoned at ~0.17 MB/s through
its filer path. Garage accepted the engine's put-object
tagging header (it does not implement the separate
PutObjectTagging API, which the engine does not call).

This is still an emulator run, not real AWS. Throughput is
local loopback + disk, not network throughput.

## Results (evidence/scaled-run-10gb-2026-11-22.json)

| Step | Result | Elapsed |
|---|---|---|
| Sync | 10,024 objects, 10,486,923,264 bytes, 13.5 MB/s, 12.9 obj/s | 776.07s |
| Verify 1 | PASS, 10,024 checked, 0 mismatches | 271.19s |
| Corrupt | 10 objects overwritten (3/2/3/2 per share) | - |
| Verify 2 | FAIL, exactly 10 mismatched | 275.54s |
| Repair sync | exactly 10 re-uploaded, 10,014 unchanged | 264.77s |
| Verify 3 | PASS, 10,024 checked, 0 mismatches | 262.64s |

Every upload is read back and re-hashed by the engine before
it counts as copied, and verify re-hashes the actual S3 bytes
(ETags are never trusted), so the sync number includes a full
read-back of every object.

## Cost model (list-price estimate, us-west-2)

Interview numbers, not a bill: 10.49 GB on real S3 Standard
is ~$0.24/month storage, ~$0.05 for 10,024 PUTs, ~$0.01 for
three full verify passes of GETs - about **$0.30 first
month** for the lab dataset. The 80 TB production story in
the README (~$1,850/month S3 Standard, Glacier lifecycle as
the lever) is unchanged.

## Reproduce

```sh
S3_ENDPOINT=http://127.0.0.1:3900 \
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
python scripts/run_scaled_demo.py --source /tmp/nas-source \
    --per-share 2500 --file-mb 1 \
    --out evidence/scaled-run.json
```

Buckets `<prefix>-<share>` must exist or be creatable at the
endpoint. Exit code 0 means verify1 PASS, verify2 caught
exactly the injected corruptions, repair re-uploaded only
those, and verify3 PASS.
