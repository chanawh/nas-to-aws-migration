"""Drill 3: DNS / endpoint failure.

Inject: a client pointed at a dead endpoint (closed port) and a
client pointed at a nonexistent hostname.
Detect: EndpointConnectionError, before any S3 call completes.
Recover: the same call against the healthy endpoint succeeds.
Backend: client-side, no S3 backend involved.
"""
import json
import time

import boto3
from botocore.config import Config
from botocore.exceptions import (ClientError, ConnectTimeoutError,
                                 EndpointConnectionError, ProxyConnectionError,
                                 ReadTimeoutError)

from common import BUCKET_PREFIX, garage_client


def _probe(endpoint):
    client = boto3.client(
        "s3", region_name="us-west-2", endpoint_url=endpoint,
        aws_access_key_id="test", aws_secret_access_key="test",
        config=Config(s3={"addressing_style": "path"},
                      connect_timeout=5, read_timeout=5,
                      retries={"max_attempts": 1}))
    client.list_buckets()


def run_once():
    t0 = time.time()
    errors = []
    for bad_endpoint in ("http://127.0.0.1:9", "http://nonexistent.invalid"):
        try:
            _probe(bad_endpoint)
            errors.append(f"{bad_endpoint}: unexpectedly succeeded")
        except (EndpointConnectionError, ReadTimeoutError,
                ConnectTimeoutError, ProxyConnectionError):
            errors.append(f"{bad_endpoint}: no response (connection/DNS failure)")
        except ClientError as e:
            errors.append(f"{bad_endpoint}: {e.response['Error']['Code']}")
    detected = all("no response" in e for e in errors)
    detect_s = round(time.time() - t0, 3)

    t0 = time.time()
    recovered = False
    try:
        garage_client().head_bucket(Bucket=f"{BUCKET_PREFIX}-finance")
        recovered = True
    except ClientError:
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
