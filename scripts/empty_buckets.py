#!/usr/bin/env python3
"""Empty the Terraform-managed S3 buckets before `terraform destroy`.

On real AWS the share buckets are created with force_destroy=false,
so `terraform destroy` refuses to delete a bucket that still holds
objects (or versions). This script deletes every object version and
delete marker in the four share buckets and the CloudTrail bucket,
so the destroy in the real-aws workflow can proceed. It is the
pre-destroy step in .github/workflows/real-aws.yml; it is not used
on emulators (there force_destroy is true and Terraform empties the
buckets itself).

The share buckets carry a GOVERNANCE Object Lock default retention
(30 days), so deleting a retained version needs the break-glass
BypassGovernanceRetention. The OIDC role has that permission.

The ransomware drill's lock-probe bucket is NOT emptied here: its
probe object is under a 1-day GOVERNANCE retention and cannot be
deleted yet, so that bucket is left and must be removed manually
after a day (see docs/REAL_AWS_ONE_DAY.md, "If the run is
orphaned"). It is not Terraform-managed, so leaving it does not
block the destroy.

Usage (credentials via environment, never committed):
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  AWS_SESSION_TOKEN=... \
  python scripts/empty_buckets.py --bucket-prefix nas2aws-migration
"""
import argparse
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

SHARES = ["finance", "it-support", "it-operations", "operations"]


def empty_bucket(s3, bucket, bypass):
    """Delete every version and delete marker in one bucket.
    Returns (versions_deleted, markers_deleted). Skips a bucket
    that does not exist."""
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            print(f"{bucket}: does not exist, skipped")
            return 0, 0
        raise
    paginator = s3.get_paginator("list_object_versions")
    to_delete = []
    versions = markers = 0
    for page in paginator.paginate(Bucket=bucket):
        for v in page.get("Versions", []):
            to_delete.append({"Key": v["Key"], "VersionId": v["VersionId"]})
            versions += 1
        for m in page.get("DeleteMarkers", []):
            to_delete.append({"Key": m["Key"], "VersionId": m["VersionId"]})
            markers += 1
        # Delete in batches of 1000 (the delete_objects limit).
        while len(to_delete) >= 1000:
            batch, to_delete = to_delete[:1000], to_delete[1000:]
            s3.delete_objects(Bucket=bucket, Delete={"Objects": batch},
                              **bypass)
    if to_delete:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete},
                          **bypass)
    print(f"{bucket}: deleted {versions} versions, {markers} delete markers")
    return versions, markers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket-prefix", default="nas2aws-migration")
    ap.add_argument("--region", default="us-west-2")
    args = ap.parse_args()

    s3 = boto3.client(
        "s3", region_name=os.environ.get("AWS_REGION", args.region),
        endpoint_url=os.environ.get("S3_ENDPOINT") or None,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        # Session token: required for temporary (OIDC/STS) credentials.
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
        config=Config(s3={"addressing_style": "path"}))

    # Same convention as the drills: bypass only on real AWS.
    bypass = ({"BypassGovernanceRetention": True}
              if not os.environ.get("S3_ENDPOINT") else {})

    buckets = [f"{args.bucket_prefix}-{s}" for s in SHARES]
    buckets.append(f"{args.bucket_prefix}-cloudtrail")
    total_v = total_m = 0
    for bucket in buckets:
        v, m = empty_bucket(s3, bucket, bypass)
        total_v += v
        total_m += m
    print(f"total: {total_v} versions, {total_m} delete markers deleted")
    print(f"note: {args.bucket_prefix}-lock-probe is not emptied here; "
          "its probe object is under 1-day retention — delete that "
          "bucket manually after a day if it exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
