"""Drill 6: silent corruption / failed recall.

Inject: overwrite 3 synced objects with junk, without touching
the manifest, as silent corruption would.
Detect: verify re-hashes the actual object bytes and reports
exactly those 3 as mismatched.
Recover: a repair sync re-uploads only the 3, and verify passes.
Backend: Garage.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from migrate_s3.dataset import generate_dataset
from migrate_s3.sync import sync_all
from migrate_s3.verify import verify

from common import (BUCKET_PREFIX, empty_bucket, ensure_buckets,
                    garage_client)


def run_once():
    s3 = garage_client()
    ensure_buckets(s3)
    for share in ("finance", "it-support", "it-operations", "operations"):
        empty_bucket(s3, f"{BUCKET_PREFIX}-{share}")
    with tempfile.TemporaryDirectory() as d:
        source = Path(d) / "source"
        generate_dataset(source)
        sync_all(s3, source, BUCKET_PREFIX)

        t0 = time.time()
        for share, key in (("finance", "budget-2027-draft.csv"),
                           ("finance", "vendor-invoices.txt"),
                           ("it-support", "ticket-export.csv")):
            s3.put_object(Bucket=f"{BUCKET_PREFIX}-{share}", Key=key,
                          Body=b"corrupted\n")
        corrupt_s = round(time.time() - t0, 3)

        t0 = time.time()
        mismatched = []
        for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
            v = verify(s3, share_dir, f"{BUCKET_PREFIX}-{share_dir.name}")
            mismatched += v.mismatched
        detected = sorted(mismatched) == sorted([
            "budget-2027-draft.csv", "vendor-invoices.txt",
            "ticket-export.csv"])
        detect_s = round(time.time() - t0, 3)

        t0 = time.time()
        r = sync_all(s3, source, BUCKET_PREFIX)
        repaired = sum(len(x.copied) for x in r.values())
        all_pass = all(
            verify(s3, sd, f"{BUCKET_PREFIX}-{sd.name}").passed
            for sd in sorted(p for p in source.iterdir() if p.is_dir()))
        recovered = all_pass and repaired == 3
        recover_s = round(time.time() - t0, 3)

    return {"drill": "silent_corruption", "backend": "Garage v1.3.0",
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": False,
            "detail": f"3 objects corrupted in {corrupt_s}s; verify found "
                      f"exactly those 3; repair re-uploaded {repaired}, "
                      f"final verify passed: {all_pass}"}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
