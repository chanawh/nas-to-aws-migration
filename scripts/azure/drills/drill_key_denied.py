"""Drill (b): key denied.

Inject: a client built with a wrong account key.
Detect: the Blob call fails with 403.
Recover: a client with the correct key reads the container.
Backend: Azurite (it validates the account key, verified live).
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure.core.exceptions import HttpResponseError

from common import (BACKEND, CONTAINER_PREFIX, blob_client, empty_all)
from migrate_s3.dataset import generate_dataset
from azure_blob.blob_sync import sync_all


def run_once():
    service = blob_client()
    empty_all(service)
    with tempfile.TemporaryDirectory() as d:
        source = Path(d) / "source"
        generate_dataset(source)
        sync_all(service, source, CONTAINER_PREFIX)

        bad = blob_client(account_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        t0 = time.time()
        detected, detail = False, ""
        try:
            bad.get_container_client(f"{CONTAINER_PREFIX}-finance").list_blobs().__next__()
            detail = "wrong key was accepted (unexpected)"
        except HttpResponseError as e:
            detected = e.status_code == 403
            detail = f"list_blobs failed with {e.status_code} {e.error_code}"
        except StopIteration:
            detail = "wrong key was accepted (unexpected)"
        detect_s = round(time.time() - t0, 3)

        t0 = time.time()
        recovered = False
        try:
            next(service.get_container_client(
                f"{CONTAINER_PREFIX}-finance").list_blobs(), None)
            recovered = True
        except HttpResponseError:
            pass
        recover_s = round(time.time() - t0, 3)

    return {"drill": "key_denied", "backend": BACKEND,
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": False, "detail": detail}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
