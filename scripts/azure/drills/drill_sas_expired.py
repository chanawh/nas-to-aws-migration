"""Drill (a): expired SAS token.

Inject: a blob SAS whose expiry is 5 minutes in the past, and one
whose start time is 5 minutes in the future.
Detect: downloading with either SAS fails with 403.
Recover: a SAS valid right now downloads the blob.
Backend: Azurite (it enforces SAS expiry, verified live).
"""
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure.core.exceptions import HttpResponseError
from azure.storage.blob import BlobSasPermissions, generate_blob_sas

from common import (ACCOUNT_KEY, ACCOUNT_NAME, BACKEND, BLOB_ENDPOINT,
                    CONTAINER_PREFIX, blob_client, empty_all)
from migrate_s3.dataset import generate_dataset
from azure_blob.blob_sync import sync_all


def _sas_url(container, blob, start, expiry):
    sas = generate_blob_sas(
        account_name=ACCOUNT_NAME, account_key=ACCOUNT_KEY,
        container_name=container, blob_name=blob,
        permission=BlobSasPermissions(read=True),
        start=start, expiry=expiry)
    return f"{BLOB_ENDPOINT}/{container}/{blob}?{sas}"


def run_once():
    service = blob_client()
    empty_all(service)
    with tempfile.TemporaryDirectory() as d:
        source = Path(d) / "source"
        generate_dataset(source)
        sync_all(service, source, CONTAINER_PREFIX)

        container = f"{CONTAINER_PREFIX}-finance"
        blob = "budget-2027-draft.csv"
        now = datetime.now(timezone.utc)
        t0 = time.time()
        errors = []
        for label, start, expiry in (
            ("expired", now - timedelta(minutes=10), now - timedelta(minutes=5)),
            ("not-yet-valid", now + timedelta(minutes=5), now + timedelta(minutes=10)),
        ):
            url = _sas_url(container, blob, start, expiry)
            from azure.storage.blob import BlobClient
            try:
                BlobClient.from_blob_url(url).download_blob().readall()
                errors.append(f"{label}: unexpectedly succeeded")
            except HttpResponseError as e:
                errors.append(f"{label}: {e.status_code} {e.error_code}")
        detected = all("403" in e for e in errors)
        detect_s = round(time.time() - t0, 3)

        t0 = time.time()
        recovered = False
        try:
            url = _sas_url(container, blob, now - timedelta(minutes=5),
                           now + timedelta(minutes=5))
            from azure.storage.blob import BlobClient
            BlobClient.from_blob_url(url).download_blob().readall()
            recovered = True
        except HttpResponseError:
            pass
        recover_s = round(time.time() - t0, 3)

    return {"drill": "expired_sas", "backend": BACKEND,
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": False, "detail": "; ".join(errors) +
            "; recovery is a SAS valid right now"}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
