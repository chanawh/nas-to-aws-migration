"""Drill 4: throttling (503 SlowDown).

What real AWS does: under request-rate pressure S3 returns 503
SlowDown, and the client must back off and retry instead of
failing the sync.
What the emulators do: neither moto nor Garage throttles. A
burst of 50 parallel puts against Garage returned zero 503s
(verified in the live path below), so genuine throttling is
real-AWS-only.
The retry code itself is exercised here against a stubbed
client that returns SlowDown twice and then succeeds, and the
live burst is recorded as the emulator's answer.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from common import BUCKET_PREFIX, empty_bucket, ensure_buckets, garage_client

RETRYABLE = {"SlowDown", "ServiceUnavailable", "InternalError", "RequestTimeout"}


def put_with_retries(s3, bucket, key, body, max_attempts=5):
    """Upload with exponential backoff on throttling errors.
    Returns (attempts_used, True) on success; raises the last
    ClientError when attempts run out. This is the code path the
    stubbed test exercises; on real AWS boto3's standard retries
    would also apply, this is the explicit second layer."""
    delay = 0.1
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=body)
            return attempt, True
        except ClientError as e:
            code = e.response["Error"]["Code"]
            last = e
            if code not in RETRYABLE or attempt == max_attempts:
                raise
            time.sleep(delay)
            delay *= 2
    raise last


def run_once():
    # Simulated path: SlowDown twice, then success.
    s3 = boto3.client("s3", region_name="us-west-2")
    stub = Stubber(s3)
    for _ in range(2):
        stub.add_client_error("put_object", "SlowDown", "Slow down")
    stub.add_response("put_object", {})
    stub.activate()
    t0 = time.time()
    attempts, ok = put_with_retries(s3, "b", "t.txt", b"x")
    detect_s = round(time.time() - t0, 3)
    stub.deactivate()
    detected = ok and attempts == 3

    # Live path: burst against Garage, count 503s.
    gs3 = garage_client()
    ensure_buckets(gs3)
    bucket = f"{BUCKET_PREFIX}-finance"
    empty_bucket(gs3, bucket)

    def one(i):
        try:
            gs3.put_object(Bucket=bucket, Key=f"burst-{i:03d}.bin", Body=b"x" * 1024)
            return None
        except ClientError as e:
            return e.response["Error"]["Code"]

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(one, range(50)))
    recover_s = round(time.time() - t0, 3)
    throttled = sum(1 for c in codes if c in RETRYABLE)
    recovered = throttled == 0 and all(c is None for c in codes)
    empty_bucket(gs3, bucket)

    return {"drill": "throttling", "backend": "Garage (live) + stub (simulated)",
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": True,
            "detail": f"stubbed SlowDown x2 then success in {attempts} attempts; "
                      f"live burst of 50 parallel puts: {throttled} throttled, "
                      f"{sum(1 for c in codes if c)} failed. Genuine throttling "
                      f"is real-AWS-only."}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
