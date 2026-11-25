"""Shared harness for the Azure Blob failure drills.

Every drill returns a dict with the same shape as scripts/drills/common.py:
    drill, backend, detected, detect_seconds, recovered,
    recover_seconds, simulated, detail

detect_seconds is the time to notice the failure; recover_seconds
is the time to get back to a verified-good state. simulated=True
means the failure itself was injected by a stub (the emulator
cannot produce it), while the detection and recovery code is the
real code path. A drill that cannot be produced on an emulator
says so in its detail instead of pretending.

Backend: Azurite is the default (local Blob emulator on
http://127.0.0.1:10000, started by scripts/start_azurite.sh with
blob versioning ON; the well-known devstore account/key is the
Azurite test credential, not a real Azure credential). Set the
three env vars below to run the same drills against real Azure:
    AZURE_BLOB_ENDPOINT=https://<account>.blob.core.windows.net
    AZURE_STORAGE_ACCOUNT_NAME=<account>
    AZURE_STORAGE_KEY=<key>
When a real account is configured, is_real_azure() is True and
BACKEND reads "real Azure Blob"; otherwise it reads
"Azurite 3.38.0". Nothing in the drills hardcodes Azurite.
"""
import json
import os
import statistics
import time
from pathlib import Path

from azure.storage.blob import BlobServiceClient

CONTAINER_PREFIX = "nas2azure-drill"
SHARES = ["finance", "it-support", "it-operations", "operations"]

DEVSTORE_ACCOUNT = "devstoreaccount1"
DEVSTORE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
    "K1SZFPTOtr/KBHBeksoGMGw=="
)
DEFAULT_BLOB_ENDPOINT = "http://127.0.0.1:10000/devstoreaccount1"

ACCOUNT_NAME = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", DEVSTORE_ACCOUNT)
ACCOUNT_KEY = os.environ.get("AZURE_STORAGE_KEY", DEVSTORE_KEY)
BLOB_ENDPOINT = os.environ.get("AZURE_BLOB_ENDPOINT")
if BLOB_ENDPOINT is None and ACCOUNT_NAME != DEVSTORE_ACCOUNT:
    # Account given, endpoint not: the standard Blob endpoint.
    BLOB_ENDPOINT = f"https://{ACCOUNT_NAME}.blob.core.windows.net"
elif BLOB_ENDPOINT is None:
    BLOB_ENDPOINT = DEFAULT_BLOB_ENDPOINT


def is_real_azure():
    """True when a real storage account is configured: an account
    name other than the devstore one, or an endpoint that is not
    localhost. Every drill branches on this."""
    if ACCOUNT_NAME != DEVSTORE_ACCOUNT:
        return True
    return ("127.0.0.1" not in BLOB_ENDPOINT
            and "localhost" not in BLOB_ENDPOINT
            and BLOB_ENDPOINT != DEFAULT_BLOB_ENDPOINT)


BACKEND = "real Azure Blob" if is_real_azure() else "Azurite 3.38.0"


def backend_label(emulator_label):
    """Honest backend label for evidence. On real Azure the label
    must say so; on Azurite it keeps the emulator's name."""
    if is_real_azure():
        return "real Azure Blob"
    return emulator_label


def blob_client(endpoint: str | None = None,
                account_name: str | None = None,
                account_key: str | None = None,
                retry_total: int | None = None) -> BlobServiceClient:
    """Blob client from the environment: real Azure when the env
    vars point at a real account, Azurite otherwise. endpoint /
    account_key overrides are how the key-denied and DNS drills
    inject their failures. retry_total=0 disables the SDK's
    default retry-with-backoff, for drills that must fail fast
    on a dead endpoint."""
    endpoint = endpoint or BLOB_ENDPOINT
    account_name = account_name or ACCOUNT_NAME
    account_key = account_key or ACCOUNT_KEY
    protocol = "https" if is_real_azure() and endpoint.startswith("https") else "http"
    conn = (
        f"DefaultEndpointsProtocol={protocol};"
        f"AccountName={account_name};AccountKey={account_key};"
        f"BlobEndpoint={endpoint};"
    )
    kwargs = {}
    if retry_total is not None:
        kwargs["retry_total"] = retry_total
    return BlobServiceClient.from_connection_string(conn, **kwargs)


def azurite_client(endpoint: str = BLOB_ENDPOINT,
                   account_key: str = ACCOUNT_KEY,
                   retry_total: int | None = None) -> BlobServiceClient:
    """Backwards-compatible alias for blob_client. The name is
    historical; the client it returns is env-aware (real Azure
    when configured, Azurite otherwise)."""
    return blob_client(endpoint=endpoint, account_key=account_key,
                       retry_total=retry_total)


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
