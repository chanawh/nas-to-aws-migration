"""Drill (e): silent corruption / failed recall.

Inject: overwrite 3 synced blobs with junk, without touching
the manifest, as silent corruption would.
Detect: verify re-hashes the actual blob bytes and reports
exactly those 3 as mismatched.
Recover: a repair sync re-uploads only the 3, and verify passes.
Backend: Azurite.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (BACKEND, CONTAINER_PREFIX, azurite_client, empty_all)
from migrate_s3.dataset import generate_dataset
from azure_blob.blob_sync import sync_all
from azure_blob.blob_verify import verify

TARGETS = (("finance", "budget-2027-draft.csv"),
           ("finance", "vendor-invoices.txt"),
           ("it-support", "ticket-export.csv"))


def run_once():
    service = azurite_client()
    empty_all(service)
    with tempfile.TemporaryDirectory() as d:
        source = Path(d) / "source"
        generate_dataset(source)
        sync_all(service, source, CONTAINER_PREFIX)

        t0 = time.time()
        for share, name in TARGETS:
            service.get_blob_client(f"{CONTAINER_PREFIX}-{share}", name) \
                .upload_blob(b"corrupted\n", overwrite=True)
        corrupt_s = round(time.time() - t0, 3)

        t0 = time.time()
        mismatched = []
        for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
            v = verify(service, share_dir, f"{CONTAINER_PREFIX}-{share_dir.name}")
            mismatched += v.mismatched
        detected = sorted(mismatched) == sorted([n for _, n in TARGETS])
        detect_s = round(time.time() - t0, 3)

        t0 = time.time()
        r = sync_all(service, source, CONTAINER_PREFIX)
        repaired = sum(len(x.copied) for x in r.values())
        all_pass = all(
            verify(service, sd, f"{CONTAINER_PREFIX}-{sd.name}").passed
            for sd in sorted(p for p in source.iterdir() if p.is_dir()))
        recovered = all_pass and repaired == 3
        recover_s = round(time.time() - t0, 3)

    return {"drill": "silent_corruption", "backend": BACKEND,
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": False,
            "detail": f"3 blobs corrupted in {corrupt_s}s; verify found "
                      f"exactly those 3; repair re-uploaded {repaired}, "
                      f"final verify passed: {all_pass}"}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
