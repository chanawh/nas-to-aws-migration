"""Shared harness for the Azure Blob failure drills (Azurite backend).

Every drill returns a dict with the same shape as scripts/drills/common.py:
    drill, backend, detected, detect_seconds, recovered,
    recover_seconds, simulated, detail

detect_seconds is the time to notice the failure; recover_seconds
is the time to get back to a verified-good state. simulated=True
means the failure itself was injected by a stub (the emulator
cannot produce it), while the detection and recovery code is the
real code path. A drill that cannot be produced on an emulator
says so in its detail instead of pretending.

Azurite is the Blob emulator on http://127.0.0.1:10000, started by
scripts/start_azurite.sh with blob versioning ON. The well-known
devstore account/key is the Azurite test credential, not a real
Azure credential.
"""
import json
import statistics
import time
from pathlib import Path

from azure.storage.blob import BlobServiceClient

CONTAINER_PREFIX = "nas2azure-drill"
SHARES = ["finance", "it-support", "it-operations", "operations"]
BLOB_ENDPOINT = "http://127.0.0.1:10000/devstoreaccount1"
ACCOUNT_NAME = "devstoreaccount1"
ACCOUNT_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
    "K1SZFPTOtr/KBHBeksoGMGw=="
)
BACKEND = "Azurite 3.38.0"


def azurite_client(endpoint: str = BLOB_ENDPOINT,
                   account_key: str = ACCOUNT_KEY,
                   retry_total: int | None = None) -> BlobServiceClient:
    """Blob client for Azurite. account_key override is how the
    key-denied drill injects a wrong key. retry_total=0 disables
    the SDK's default retry-with-backoff, for drills that must
    fail fast on a dead endpoint."""
    conn = (
        "DefaultEndpointsProtocol=http;"
        f"AccountName={ACCOUNT_NAME};AccountKey={account_key};"
        f"BlobEndpoint={endpoint};"
    )
    kwargs = {}
    if retry_total is not None:
        kwargs["retry_total"] = retry_total
    return BlobServiceClient.from_connection_string(conn, **kwargs)


def ensure_containers(service: BlobServiceClient) -> None:
    for share in SHARES:
        try:
            service.create_container(f"{CONTAINER_PREFIX}-{share}")
        except Exception:
            pass


def empty_container(service: BlobServiceClient, container: str) -> None:
    cc = service.get_container_client(container)
    for blob in cc.list_blobs():
        cc.get_blob_client(blob.name).delete_blob()


def empty_all(service: BlobServiceClient) -> None:
    ensure_containers(service)
    for share in SHARES:
        empty_container(service, f"{CONTAINER_PREFIX}-{share}")


def timed(fn):
    t0 = time.time()
    out = fn()
    return out, round(time.time() - t0, 3)


def run_n(name, fn, n=3):
    """Run a drill n times, return the record with medians."""
    runs = []
    for _ in range(n):
        runs.append(fn())
    rec = dict(runs[0])
    rec["runs"] = runs
    for key in ("detect_seconds", "recover_seconds"):
        vals = [r[key] for r in runs if isinstance(r.get(key), (int, float))]
        rec["median_" + key] = round(statistics.median(vals), 3) if vals else None
    rec["detected_runs"] = sum(1 for r in runs if r.get("detected"))
    rec["recovered_runs"] = sum(1 for r in runs if r.get("recovered"))
    return rec


def write_evidence(path, payload):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2))
    return str(p)
