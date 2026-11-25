"""Shared harness for the failure drills.

Every drill returns a dict with the same shape:
    drill, backend, detected, detect_seconds, recovered,
    recover_seconds, simulated, detail

detect_seconds is the time to notice the failure; recover_seconds
is the time to get back to a verified-good state. simulated=True
means the failure itself was injected by a stub (the emulator
cannot produce it), while the detection and recovery code is the
real code path. A drill that cannot be produced on an emulator
says so in its detail instead of pretending.
"""
import json
import os
import statistics
import time
from pathlib import Path

import boto3
from botocore.config import Config

BUCKET_PREFIX = "nas2aws-migration"
SHARES = ["finance", "it-support", "it-operations", "operations"]


def garage_client():
    """S3 client for the local Garage emulator, creds from env.
    An empty S3_ENDPOINT means real AWS (no endpoint override)."""
    endpoint = os.environ.get("S3_ENDPOINT") or None
    return boto3.client(
        "s3", region_name=os.environ.get("AWS_REGION", "us-west-2"),
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        # Session token: required for temporary (OIDC/STS) credentials.
        # None when unset (emulators), which boto3 treats as no token.
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
        config=Config(s3={"addressing_style": "path"}))


def is_real_aws():
    """True when no S3_ENDPOINT is set: boto3 talks to real AWS,
    not to moto or Garage. Every drill branches on this."""
    return not os.environ.get("S3_ENDPOINT")


def backend_label(emulator_label):
    """Honest backend label for evidence. On real AWS the label
    must say so; on an emulator it keeps the emulator's name.
    Hardcoding an emulator name here would mislabel a real run."""
    if is_real_aws():
        return f"real AWS S3 ({os.environ.get('AWS_REGION', 'us-west-2')})"
    return emulator_label


def empty_bucket(s3, bucket):
    # On real AWS the share buckets carry a GOVERNANCE Object Lock
    # default retention (30 days), so deleting a retained object
    # needs the break-glass bypass. On emulators there is no
    # retention, so the parameter is omitted (moto/Garage do not
    # implement it). Real AWS is when S3_ENDPOINT is unset.
    kwargs = ({"BypassGovernanceRetention": True}
              if not os.environ.get("S3_ENDPOINT") else {})
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=o["Key"], **kwargs)


def ensure_buckets(s3):
    for s in SHARES:
        try:
            s3.create_bucket(Bucket=f"{BUCKET_PREFIX}-{s}")
        except Exception:
            pass


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
