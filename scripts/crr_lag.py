#!/usr/bin/env python3
"""Measure S3 cross-region replication lag with a canary object.

PUTs a small canary object to a share bucket (us-west-2), then
polls the replica bucket (us-east-1) with head_object until the
object appears, and records the lag: replica-visible time minus
PUT time. Writes evidence JSON.

Real-AWS-only: Garage and moto do not replicate, so on an emulator
the script refuses to run and records "not measured" with the
reason, never a fake number. It also fails cleanly (and records
"not measured") when replication is not configured on the source
bucket, or when the canary never appears within the timeout.

The canary is deleted from the source bucket afterwards (best
effort). The delete replicates as a delete marker to the replica
(delete marker replication is on by default); that is expected
and is not a failure.

Usage (credentials via environment, never committed):
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  AWS_SESSION_TOKEN=... \
  python scripts/crr_lag.py --bucket nas2aws-migration-finance \
      --replica-bucket nas2aws-migration-finance-replica \
      --out evidence/crr-lag-2026-11-25.json
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "drills"))

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from common import is_real_aws


class ReplicationNotConfigured(Exception):
    """The source bucket has no replication configuration."""


class ReplicationTimeout(Exception):
    """The canary never appeared in the replica bucket."""


def check_replication_config(s3, bucket: str) -> bool:
    """True when the bucket has a replication configuration.
    False when the API reports none (NoSuchReplicationConfiguration)."""
    try:
        s3.get_bucket_replication(Bucket=bucket)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchReplicationConfiguration":
            return False
        raise


def poll_until_visible(head_fn, timeout_s: float, interval_s: float,
                       clock=time.time, sleep_fn=time.sleep) -> float | None:
    """Poll head_fn until it returns True (the object is visible) or
    the timeout. Returns the visible timestamp (from clock), or None
    on timeout. clock and sleep_fn are injectable so tests use fake
    time instead of waiting in real time."""
    deadline = clock() + timeout_s
    while True:
        if head_fn():
            return clock()
        if clock() > deadline:
            return None
        sleep_fn(interval_s)


def measure_lag(s3, replica_s3, bucket: str, replica_bucket: str,
                canary_key: str, timeout_s: float = 900,
                interval_s: float = 5, clock=time.time,
                sleep_fn=time.sleep) -> dict:
    """PUT the canary, poll the replica, return the lag record.
    Raises ReplicationNotConfigured or ReplicationTimeout. clock
    and sleep_fn are injectable so tests use fake time."""
    if not check_replication_config(s3, bucket):
        raise ReplicationNotConfigured(
            f"no replication configuration on {bucket}; "
            "apply Terraform with enable_crr=true first")
    canary = {"canary": canary_key, "bucket": bucket,
              "replica_bucket": replica_bucket}.__str__().encode("utf-8")
    s3.put_object(Bucket=bucket, Key=canary_key, Body=canary)
    t_put = clock()  # PUT completed; lag is PUT -> replica visible.

    def head():
        try:
            replica_s3.head_object(Bucket=replica_bucket, Key=canary_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise

    t_visible = poll_until_visible(head, timeout_s, interval_s,
                                   clock=clock, sleep_fn=sleep_fn)
    if t_visible is None:
        raise ReplicationTimeout(
            f"canary {canary_key} did not appear in {replica_bucket} "
            f"within {timeout_s}s; replication may be misconfigured")
    # Best-effort cleanup: the delete replicates as a delete marker.
    try:
        s3.delete_object(Bucket=bucket, Key=canary_key)
    except ClientError:
        pass
    return {"canary_key": canary_key, "put_at": t_put,
            "visible_at": t_visible,
            "lag_seconds": round(t_visible - t_put, 3),
            "status": "measured"}


def _client(region: str):
    return boto3.client(
        "s3", region_name=region,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
        config=Config(s3={"addressing_style": "path"}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True,
                    help="Source share bucket (primary region)")
    ap.add_argument("--replica-bucket", required=True,
                    help="Destination bucket (replica region)")
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--replica-region", default="us-east-1")
    ap.add_argument("--timeout", type=float, default=900,
                    help="Seconds to wait for the canary (default 900)")
    ap.add_argument("--poll-interval", type=float, default=5)
    ap.add_argument("--canary-key", default=None)
    ap.add_argument("--out", default="evidence/crr-lag.json")
    args = ap.parse_args()

    canary_key = (args.canary_key or
                  f"_crr-canary-{time.strftime('%Y%m%dT%H%M%S')}.json")
    evidence: dict = {
        "backend": f"real AWS S3 ({args.region} -> {args.replica_region})",
        "source_bucket": args.bucket,
        "replica_bucket": args.replica_bucket,
        "canary_key": canary_key,
        "status": "not_measured", "reason": ""}
    exit_code = 0

    if not is_real_aws():
        evidence["reason"] = ("CRR is real-AWS-only; Garage/moto do not "
                              "replicate")
        print(evidence["reason"], file=sys.stderr)
        exit_code = 2
    else:
        s3 = _client(args.region)
        replica_s3 = _client(args.replica_region)
        try:
            rec = measure_lag(s3, replica_s3, args.bucket,
                              args.replica_bucket, canary_key,
                              args.timeout, args.poll_interval)
            evidence.update(rec)
            print(f"lag: {rec['lag_seconds']}s "
                  f"(PUT -> replica visible)")
        except ReplicationNotConfigured as e:
            evidence["reason"] = str(e)
            print(str(e), file=sys.stderr)
            exit_code = 1
        except ReplicationTimeout as e:
            evidence["reason"] = str(e)
            print(str(e), file=sys.stderr)
            exit_code = 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2))
    print(f"saved {out}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
