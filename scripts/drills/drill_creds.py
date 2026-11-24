"""Drill 1: expired / invalid credentials.

Inject: a client with an unknown access key.
Detect: the S3 call fails with 403.
Recover: a client with valid credentials reads the bucket.
Backend: Garage (it validates access keys; moto accepts anything).
"""
import json
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from common import BUCKET_PREFIX, garage_client


def run_once():
    endpoint = os.environ["S3_ENDPOINT"]
    bad = boto3.client(
        "s3", region_name="us-west-2", endpoint_url=endpoint,
        aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
        aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        config=Config(s3={"addressing_style": "path"}))
    t0 = time.time()
    detected, detail = False, ""
    try:
        bad.list_buckets()
        detail = "bad credentials were accepted (unexpected)"
    except ClientError as e:
        code = e.response["Error"]["Code"]
        detected = code in ("AccessDenied", "InvalidAccessKeyId",
                            "SignatureDoesNotMatch")
        detail = f"list_buckets failed with {code}"
    detect_s = round(time.time() - t0, 3)

    t0 = time.time()
    recovered = False
    try:
        garage_client().head_bucket(Bucket=f"{BUCKET_PREFIX}-finance")
        recovered = True
    except ClientError as e:
        detail += f"; recovery failed: {e.response['Error']['Code']}"
    recover_s = round(time.time() - t0, 3)

    return {"drill": "expired_credentials", "backend": "Garage v1.3.0",
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": False, "detail": detail}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
