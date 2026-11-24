"""Drill (c): DNS / endpoint failure.

Inject: a client pointed at dead endpoints (closed local ports).
Detect: a connection error, before any Blob call completes.
Recover: the same call against the healthy endpoint succeeds.
Backend: client-side, no Blob backend involved.

Note: the S3 twin of this drill also probed a nonexistent
hostname, but this sandbox blocks egress to unresolvable hosts
(the probe hangs on an access prompt instead of failing fast),
so the Azure drill uses two closed localhost ports. Both prove
the same thing: the client fails before any Blob call completes,
and the same call against the healthy endpoint succeeds.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure.core.exceptions import ServiceRequestError

from common import BACKEND, CONTAINER_PREFIX, azurite_client


def _probe(endpoint):
    # retry_total=0: the SDK's default retry policy would otherwise
    # retry a refused connection with exponential backoff (up to
    # ~120 s per probe). A dead endpoint must fail fast.
    client = azurite_client(endpoint=endpoint, retry_total=0)
    next(client.list_containers(), None)


def run_once():
    t0 = time.time()
    errors = []
    for bad_endpoint in ("http://127.0.0.1:9/devstoreaccount1",
                         "http://127.0.0.1:59999/devstoreaccount1"):
        try:
            _probe(bad_endpoint)
            errors.append(f"{bad_endpoint}: unexpectedly succeeded")
        except ServiceRequestError:
            errors.append(f"{bad_endpoint}: no response (connection/DNS failure)")
        except Exception as e:
            errors.append(f"{bad_endpoint}: {type(e).__name__}: {e}")
    detected = all("no response" in e for e in errors)
    detect_s = round(time.time() - t0, 3)

    t0 = time.time()
    recovered = False
    try:
        next(azurite_client().list_containers(), None)
        recovered = True
    except Exception:
        pass
    recover_s = round(time.time() - t0, 3)

    return {"drill": "dns_endpoint_failure", "backend": "client-side",
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": False,
            "detail": "; ".join(errors) + "; recovery is the same call "
                      "against the healthy endpoint"}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
