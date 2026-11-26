#!/usr/bin/env python3
"""Failover drill: verify the CRR replica, then record the failover.

Verifies that the replica bucket in the second region holds the
objects from the source bucket's manifest (SHA-256 re-hash of the
actual bytes, the same verification the engine uses), then records
the failover steps: the replica bucket becomes the read source, and
a real failover re-points the application to it. There is no
application here, so the steps are recorded, not executed -- the
evidence is the verification result plus the step list, not a
claim that an application was failed over.

Real-AWS-only: Garage and moto do not replicate, so on an emulator
the script refuses to run and records "not measured" with the
reason, never a fake number. It also records "not measured" when
the source bucket has no manifest (run the sync first) or the
replica bucket is empty.

Note on delete markers: replication copies delete markers too, so
a deleted object in the primary is a delete marker in the replica
as well. This drill verifies the object versions (the manifest's
SHA-256 values) -- a version that is present but behind a delete
marker is reported as missing, because a read of that key returns
the marker, not the version. The pre-attack versions are still in
the replica; restoring them is a version-level restore, not a
plain read.

Usage (credentials via environment, never committed):
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  AWS_SESSION_TOKEN=... \
  python scripts/crr_failover.py --bucket nas2aws-migration-finance \
      --replica-bucket nas2aws-migration-finance-replica \
      --out evidence/crr-failover-2026-11-25.json
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "drills"))

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from migrate_s3.sync import load_manifest

from common import is_real_aws

FAILOVER_STEPS = [
    "1. Confirm the replica bucket holds the objects: count + SHA-256 "
    "verify against the source manifest (this drill's verification).",
    "2. Re-point the application's read path to the replica bucket "
    "(<prefix>-<share>-replica in the replica region).",
    "3. Promote the replica to the write target: accept writes to the "
    "replica bucket, or create a reverse replication rule back to "
    "the primary region.",
    "4. Verify a read from the replica (the verify pass above is the "
    "evidence).",
]


def verify_replica(replica_s3, replica_bucket: str,
                   manifest: dict[str, dict]) -> dict:
    """Re-hash every manifest object in the replica bucket and
    compare against the manifest's SHA-256. Returns {checked, ok,
    missing, mismatched, passed}. A key whose current version is a
    delete marker reads as missing (see the module docstring)."""
    ok, missing, mismatched = [], [], []
    for rel in sorted(manifest):
        try:
            body = replica_s3.get_object(
                Bucket=replica_bucket, Key=rel)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                missing.append(rel)
                continue
            raise
        if hashlib.sha256(body).hexdigest() != manifest[rel]["sha256"]:
            mismatched.append(rel)
        else:
            ok.append(rel)
    return {"checked": len(manifest), "ok": ok, "missing": missing,
            "mismatched": mismatched,
            "passed": not missing and not mismatched}


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
    ap.add_argument("--out", default="evidence/crr-failover.json")
    args = ap.parse_args()

    evidence: dict = {
        "backend": f"real AWS S3 ({args.region} -> {args.replica_region})",
        "source_bucket": args.bucket,
        "replica_bucket": args.replica_bucket,
        "failover_steps": FAILOVER_STEPS,
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
        manifest = load_manifest(s3, args.bucket)
        if not manifest:
            evidence["reason"] = (
                f"no manifest in {args.bucket}; run the sync first")
            print(evidence["reason"], file=sys.stderr)
            exit_code = 1
        else:
            try:
                replica_s3.head_bucket(Bucket=args.replica_bucket)
            except ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
                    evidence["reason"] = (
                        f"replica bucket {args.replica_bucket} does not "
                        "exist; apply Terraform with enable_crr=true first")
                    print(evidence["reason"], file=sys.stderr)
                    exit_code = 1
                    manifest = None
                else:
                    raise
            if manifest is not None:
                result = verify_replica(replica_s3, args.replica_bucket,
                                        manifest)
                evidence["verify"] = result
                if result["passed"]:
                    evidence["status"] = "measured"
                    print(f"failover verify: passed "
                          f"checked={result['checked']}")
                else:
                    evidence["reason"] = (
                        f"replica verification failed: "
                        f"{len(result['missing'])} missing, "
                        f"{len(result['mismatched'])} mismatched")
                    print(evidence["reason"], file=sys.stderr)
                    exit_code = 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2))
    print(f"saved {out}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
