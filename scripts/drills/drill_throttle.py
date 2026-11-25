"""Drill 4: throttling (503 SlowDown).

What real AWS does: under request-rate pressure S3 returns 503
SlowDown, and the client must back off and retry instead of
failing the sync.
What the emulators do: neither moto nor Garage throttles. A
burst of 50 parallel puts against Garage returned zero 503s
(verified in the live path below), so genuine throttling is
real-AWS-only. On real AWS the drill runs a large parallel
burst (32 workers x 200 puts, one prefix) and counts genuine
503 SlowDown responses; if S3 absorbs the burst without
throttling, the drill says so honestly instead of claiming a
throttle it never saw.
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

from common import (BUCKET_PREFIX, backend_label, empty_bucket,
                    ensure_buckets, garage_client, is_real_aws)

RETRYABLE = {"SlowDown", "ServiceUnavailable", "InternalError", "RequestTimeout"}

# Real-AWS burst: 32 workers x 200 puts against one prefix, so
# the whole burst lands on one S3 partition. S3 scales fast, so
# genuine SlowDown is not guaranteed even at this rate — the
# drill reports honestly when it does not happen instead of
# claiming a throttle it never saw.


def put_with_retries(s3, bucket, key, body, max_attempts=5, codes_seen=None):
    """Upload with exponential backoff on throttling errors.
    Returns (attempts_used, True) on success; raises the last
    ClientError when attempts run out. This is the code path the
    stubbed test exercises; on real AWS boto3's standard retries
    would also apply, this is the explicit second layer.
    codes_seen, when given a list, collects every error code the
    retries met, so the real burst can count genuine SlowDowns."""
    delay = 0.1
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=body)
            return attempt, True
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if codes_seen is not None:
                codes_seen.append(code)
            last = e
            if code not in RETRYABLE or attempt == max_attempts:
                raise
            time.sleep(delay)
            delay *= 2
    raise last


def _run_real():
    """Real AWS: a large parallel burst against one prefix, counting
    genuine 503 SlowDown responses. Each put goes through
    put_with_retries, so a throttled put that recovers on retry is
    counted as throttled AND recovered. simulated=False only when
    at least one genuine SlowDown was seen; if S3 absorbed the
    burst without throttling, that is the honest result."""
    s3 = garage_client()
    ensure_buckets(s3)
    bucket = f"{BUCKET_PREFIX}-finance"
    empty_bucket(s3, bucket)
    workers, puts = 32, 200

    def one(i):
        codes = []
        try:
            attempts, _ = put_with_retries(
                s3, bucket, f"burst-{i:05d}.bin", b"x" * 1024,
                codes_seen=codes)
            return attempts, codes, None
        except ClientError as e:
            return None, codes, e.response["Error"]["Code"]

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, range(workers * puts)))
    burst_s = round(time.time() - t0, 3)
    slowdowns = sum(codes.count("SlowDown")
                    for _, codes, _ in results)
    failed = [c for _, _, c in results if c is not None]
    # Recover: any put that failed outright is retried to completion.
    t0 = time.time()
    for i, (attempts, codes, code) in enumerate(results):
        if code is not None:
            put_with_retries(s3, bucket, f"burst-{i:05d}.bin", b"x" * 1024)
    recover_s = round(time.time() - t0, 3)
    # list_objects_v2 returns at most 1000 keys per call; paginate.
    present = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="burst-"):
        present += len(page.get("Contents", []))
    recovered = present == workers * puts and not failed
    empty_bucket(s3, bucket)
    detected = slowdowns > 0
    return {"drill": "throttling", "backend": backend_label(""),
            "detected": detected, "detect_seconds": burst_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": not detected,
            "detail": f"real burst of {workers * puts} parallel puts "
                      f"({workers} workers, one prefix): {slowdowns} "
                      f"genuine SlowDown responses, {len(failed)} failed "
                      f"outright, {present}/{workers * puts} present after "
                      f"recovery. " + ("Genuine throttling measured."
                      if detected else "No throttling occurred: S3 "
                      "absorbed the burst. simulated=True because no "
                      "genuine 503 was observed.")}


def run_once():
    if is_real_aws():
        return _run_real()
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

    return {"drill": "throttling", "backend": backend_label("Garage (live) + stub (simulated)"),
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": True,
            "detail": f"stubbed SlowDown x2 then success in {attempts} attempts; "
                      f"live burst of 50 parallel puts: {throttled} throttled, "
                      f"{sum(1 for c in codes if c)} failed. Genuine throttling "
                      f"is real-AWS-only."}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
