"""Drill (throttling): simulated 503 retry.

What real Azure does: under request-rate pressure Blob Storage
returns 503 ServerBusy, and the client must back off and retry
instead of failing the sync.
What Azurite does: it never throttles. A burst of 50 parallel
1 KB uploads against Azurite returned zero 503s (verified in the
live path below), so genuine throttling is real-Azure-only.
The retry code itself is exercised here against a stubbed
uploader that raises 503 twice and then succeeds, and the live
burst is recorded as the emulator's answer. This drill is
labelled simulated - do not claim Azurite throttled.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure.core.exceptions import HttpResponseError

from common import (BACKEND, CONTAINER_PREFIX, azurite_client,
                    empty_all, ensure_containers)

RETRYABLE = {503, 500, 408}


def upload_with_retries(upload_fn, max_attempts=5):
    """Call upload_fn with exponential backoff on retryable HTTP
    statuses. Returns (attempts_used, True) on success; raises the
    last HttpResponseError when attempts run out. This is the code
    path the stubbed test exercises; on real Azure the SDK's
    built-in retry policy would also apply, this is the explicit
    second layer."""
    delay = 0.1
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            upload_fn()
            return attempt, True
        except HttpResponseError as e:
            last = e
            if e.status_code not in RETRYABLE or attempt == max_attempts:
                raise
            time.sleep(delay)
            delay *= 2
    raise last


class _StubUploader:
    """Simulated throttling: raises 503 twice, then succeeds."""

    def __init__(self, failures=2):
        self.calls = 0
        self.failures = failures

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            err = HttpResponseError("simulated 503 ServerBusy")
            err.status_code = 503
            raise err
        return None




def run_once():
    # Simulated path: 503 twice, then success.
    stub = _StubUploader(failures=2)
    t0 = time.time()
    attempts, ok = upload_with_retries(stub)
    detect_s = round(time.time() - t0, 3)
    detected = ok and attempts == 3

    # Live path: burst against Azurite, count 503s.
    service = azurite_client()
    ensure_containers(service)
    container = f"{CONTAINER_PREFIX}-finance"
    empty_all(service)

    def one(i):
        try:
            service.get_blob_client(container, f"burst-{i:03d}.bin") \
                .upload_blob(b"x" * 1024, overwrite=True)
            return None
        except HttpResponseError as e:
            return e.status_code

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(one, range(50)))
    recover_s = round(time.time() - t0, 3)
    throttled = sum(1 for c in codes if c in RETRYABLE)
    recovered = throttled == 0 and all(c is None for c in codes)
    empty_all(service)

    return {"drill": "throttling", "backend": "Azurite (live) + stub (simulated)",
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": True,
            "detail": f"stubbed 503 x2 then success in {attempts} attempts; "
                      f"live burst of 50 parallel puts: {throttled} throttled, "
                      f"{sum(1 for c in codes if c)} failed. Genuine throttling "
                      f"is real-Azure-only."}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
