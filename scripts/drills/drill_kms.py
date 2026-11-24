"""Drill 2: KMS denied.

What real AWS does: a PutObject with a disabled, pending-deletion,
or policy-denied KMS key fails with AccessDenied /
KMSInvalidStateException, and the object is never written.
What the emulators do: moto accepts the put regardless of key
state (verified 2026-11-24: disabled key, pending-deletion key,
and unknown key ID all succeed), and Garage has no KMS at all.
So the live path records that non-enforcement, and the
detect/recover path is exercised against a stubbed client that
raises the real AWS error codes. Genuine KMS denial is
real-AWS-only.
"""
import json
import time

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from moto import mock_aws

from common import BUCKET_PREFIX


def _put_with_kms(s3, bucket, key, body, kms_key_id):
    """The code path under test: one KMS-encrypted put, with the
    KMS error surfaced as a distinct failure instead of a generic
    upload error."""
    s3.put_object(Bucket=bucket, Key=key, Body=body,
                  ServerSideEncryption="aws:kms", SSEKMSKeyId=kms_key_id)


def run_once():
    # Live path: moto does not enforce KMS key state.
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-west-2")
        kms = boto3.client("kms", region_name="us-west-2")
        s3.create_bucket(Bucket=f"{BUCKET_PREFIX}-finance",
                         CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
        kid = kms.create_key(Description="drill")["KeyMetadata"]["KeyId"]
        kms.disable_key(KeyId=kid)
        try:
            _put_with_kms(s3, f"{BUCKET_PREFIX}-finance", "k.txt", b"x", kid)
            live = "moto accepted a put with a DISABLED KMS key (no enforcement)"
        except ClientError as e:
            live = f"moto rejected the put: {e.response['Error']['Code']}"

    # Simulated path: the stub raises the real AWS error, then the
    # retry with an enabled key succeeds. This exercises the
    # detect -> recover code, not the emulator.
    s3 = boto3.client("s3", region_name="us-west-2")
    stub = Stubber(s3)
    stub.add_client_error("put_object", "KMSInvalidStateException",
                          "The key is disabled")
    stub.add_response("put_object", {})
    stub.activate()
    t0 = time.time()
    detected = False
    try:
        _put_with_kms(s3, "b", "k.txt", b"x", "disabled-key-id")
    except ClientError as e:
        detected = e.response["Error"]["Code"] == "KMSInvalidStateException"
    detect_s = round(time.time() - t0, 3)
    t0 = time.time()
    recovered = False
    try:
        _put_with_kms(s3, "b", "k.txt", b"x", "enabled-key-id")
        recovered = True
    except ClientError:
        pass
    recover_s = round(time.time() - t0, 3)
    stub.deactivate()

    return {"drill": "kms_denied", "backend": "moto (live) + stub (simulated)",
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": True,
            "detail": live + "; stubbed path raises KMSInvalidStateException, "
                      "retry with an enabled key succeeds. Genuine KMS "
                      "denial is real-AWS-only."}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
