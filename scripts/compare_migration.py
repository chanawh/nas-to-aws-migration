#!/usr/bin/env python3
"""Compare the same migration three ways, on the same dataset.

Methods, each writing to its own buckets (<prefix>-<method>-<share>):
  engine  - the Python sync engine (sync_all + verify). Per-object
            SHA-256 verification, re-hash actual bytes.
  s3sync  - `aws s3 sync` (AWS CLI). Compares by size/mtime and
            does NOT do per-object SHA-256 verification, so the
            engine's verify pass runs over the s3-sync'd bucket
            afterwards as the independent correctness check.
  datasync - AWS DataSync, task from the on-prem NAS (via an agent)
            to the finance S3 bucket (terraform, enable_datasync_s3).
            DataSync is a managed service: it is not emulated, so
            this method is real-AWS-only and is recorded as skipped
            on emulators, with the reason, not a fake number.
            DataSync covers the finance share only (one task is one
            source/destination pair); engine and s3sync cover all
            four shares. The evidence records the share scope per
            method, so the comparison is never apples-to-oranges
            by accident.

Emulator reality: engine + s3sync run against Garage (s3 sync
with --endpoint-url). The 24-file demo dataset is the emulator
comparison; the 10 GB comparison runs on real AWS (see
docs/COMPARISON.md). DataSync cannot run on emulators.

Usage (credentials via environment, never committed):
  S3_ENDPOINT=http://127.0.0.1:3900 \
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  S3_BACKEND_LABEL="Garage v1.3.0 (local disk-backed emulator)" \
  python scripts/compare_migration.py --source <dataset> \
      --methods engine,s3sync --out evidence/comparison-2026-11-25.json
  # Real AWS, with DataSync:
  DATASYNC_TASK_ARN=<task arn> \
  python scripts/compare_migration.py --source <dataset> \
      --methods engine,s3sync,datasync --out evidence/comparison-real-aws.json
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "drills"))

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from migrate_s3.sync import sync_all
from migrate_s3.verify import verify

from common import backend_label, empty_bucket, garage_client, is_real_aws

SHARES = ["finance", "it-support", "it-operations", "operations"]
CONTROL_FILES = ("quotas.json", "acl.json", "users.csv")


def method_bucket(bucket_prefix: str, method: str, share: str) -> str:
    """One bucket set per method, so the three runs never interfere."""
    return f"{bucket_prefix}-{method}-{share}"


def create_bucket(s3, bucket: str, region: str) -> None:
    """Create a bucket in the right region. us-east-1 takes no
    LocationConstraint; every other region requires one. A bucket
    that already exists (owned by us) is not an error."""
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": region})
    except ClientError as e:
        if e.response["Error"]["Code"] not in (
                "BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise


def dataset_stats(source: Path) -> dict:
    files = bytes_total = 0
    for f in source.rglob("*"):
        if f.is_file() and f.name not in CONTROL_FILES:
            files += 1
            bytes_total += f.stat().st_size
    return {"files": files, "bytes": bytes_total}


def bucket_stats(s3, bucket: str) -> dict:
    """Objects and bytes actually in a bucket (excludes manifests)."""
    objects = bytes_total = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            if obj["Key"] == "_manifest.json":
                continue
            objects += 1
            bytes_total += obj["Size"]
    return {"objects": objects, "bytes": bytes_total}


def run_engine_method(s3, source: Path, bucket_prefix: str,
                      region: str, workers: int,
                      client_factory) -> dict:
    """The Python engine, all four shares. Returns the method record."""
    for share in SHARES:
        bucket = method_bucket(bucket_prefix, "engine", share)
        create_bucket(s3, bucket, region)
        # Empty first: every run is a full upload, so the MB/s is
        # comparable (a re-run against a full bucket copies 0).
        empty_bucket(s3, bucket)
    t0 = time.time()
    results = sync_all(s3, source, f"{bucket_prefix}-engine",
                       workers=workers, client_factory=client_factory)
    elapsed = round(time.time() - t0, 3)
    copied = sum(len(r.copied) for r in results.values())
    bytes_copied = sum(r.bytes_copied for r in results.values())
    checked = mismatched = 0
    all_pass = True
    for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        v = verify(s3, share_dir,
                   method_bucket(bucket_prefix, "engine", share_dir.name))
        checked += v.checked
        mismatched += len(v.mismatched) + len(v.missing)
        all_pass = all_pass and v.passed
    return {"ran": True, "shares": SHARES,
            "objects": copied, "bytes": bytes_copied,
            "seconds": elapsed,
            "mb_s": round(bytes_copied / elapsed / 1e6, 2) if elapsed else 0,
            "verify": {"checked": checked, "mismatched": mismatched,
                       "passed": all_pass},
            "verification": "per-object SHA-256 (engine verify)",
            "notes": ["Engine uploads only changed objects (SHA-256 "
                      "compared against the bytes in S3); a re-run "
                      "copies 0."]}


def find_aws_cli() -> str | None:
    """The `aws` binary. On PATH first; otherwise next to the Python
    running this script (pip install awscli into the project venv
    puts it there, and that bin dir is not on PATH when the script
    is invoked via the venv's python)."""
    found = shutil.which("aws")
    if found:
        return found
    # absolute(), not resolve(): the venv's python is a symlink to
    # /usr/bin/python3.12, and resolve() would follow it to /usr/bin.
    candidate = Path(sys.executable).absolute().parent / "aws"
    return str(candidate) if candidate.is_file() else None


def build_s3sync_argv(aws_bin: str, source_share: Path, bucket: str,
                      endpoint_url: str | None, region: str) -> list[str]:
    """The `aws s3 sync` invocation. --endpoint-url only on an
    emulator; on real AWS the CLI uses the real endpoint."""
    argv = [aws_bin, "s3", "sync", str(source_share), f"s3://{bucket}",
            "--region", region]
    if endpoint_url:
        argv += ["--endpoint-url", endpoint_url]
    return argv


def run_s3sync_method(source: Path, bucket_prefix: str,
                      endpoint_url: str | None, region: str,
                      s3, aws_bin: str | None = None,
                      run_cmd=None) -> dict:
    """`aws s3 sync`, all four shares, then the engine's verify pass
    over each s3-sync'd bucket (s3 sync does no per-object SHA-256
    verification of its own). run_cmd is injectable for tests."""
    aws_bin = aws_bin or find_aws_cli()
    if not aws_bin:
        return {"ran": False,
                "skipped_reason": "aws CLI not found on PATH"}
    run_cmd = run_cmd or subprocess.run
    for share in SHARES:
        bucket = method_bucket(bucket_prefix, "s3sync", share)
        create_bucket(s3, bucket, region)
        empty_bucket(s3, bucket)
    t0 = time.time()
    for share in SHARES:
        argv = build_s3sync_argv(
            aws_bin, source / share,
            method_bucket(bucket_prefix, "s3sync", share),
            endpoint_url, region)
        proc = run_cmd(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            return {"ran": False,
                    "skipped_reason": f"aws s3 sync failed for {share}: "
                    f"{(proc.stderr or '').strip()[:300]}"}
    elapsed = round(time.time() - t0, 3)
    objects = bytes_total = 0
    for share in SHARES:
        stats = bucket_stats(s3, method_bucket(bucket_prefix, "s3sync", share))
        objects += stats["objects"]
        bytes_total += stats["bytes"]
    checked = mismatched = 0
    all_pass = True
    for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        v = verify(s3, share_dir,
                   method_bucket(bucket_prefix, "s3sync", share_dir.name))
        checked += v.checked
        mismatched += len(v.mismatched) + len(v.missing)
        all_pass = all_pass and v.passed
    return {"ran": True, "shares": SHARES,
            "objects": objects, "bytes": bytes_total,
            "seconds": elapsed,
            "mb_s": round(bytes_total / elapsed / 1e6, 2) if elapsed else 0,
            "verify": {"checked": checked, "mismatched": mismatched,
                       "passed": all_pass},
            "verification": "engine verify pass over the s3-sync'd "
                            "bucket (s3 sync itself does no per-object "
                            "SHA-256 verification)",
            "notes": ["s3 sync compares by size/mtime by default, "
                      "not by checksum; a corrupted object with the "
                      "same size/mtime is not re-uploaded."]}


def wait_task_execution(client, execution_arn: str, timeout_s: int = 1800,
                        interval_s: int = 15, sleep_fn=time.sleep) -> dict:
    """Poll a DataSync task execution until it finishes. Returns the
    final describe_task_execution record. sleep_fn is injectable so
    tests do not wait in real time."""
    deadline = time.time() + timeout_s
    while True:
        resp = client.describe_task_execution(TaskExecutionArn=execution_arn)
        status = resp.get("Status")
        if status in ("SUCCESS", "ERROR"):
            return resp
        if time.time() > deadline:
            raise TimeoutError(
                f"DataSync execution {execution_arn} did not finish "
                f"in {timeout_s}s (last status {status})")
        sleep_fn(interval_s)


def run_datasync_method(task_arn: str | None, s3, source: Path,
                        bucket_prefix: str, region: str, client=None,
                        real_aws: bool | None = None) -> dict:
    """DataSync, finance share only (one task is one source/destination
    pair). Real-AWS-only: on an emulator it is recorded as skipped.
    client and real_aws are injectable for tests."""
    real_aws = is_real_aws() if real_aws is None else real_aws
    if not real_aws:
        return {"ran": False,
                "skipped_reason": "DataSync is a managed service; "
                "not emulated by moto/Garage"}
    if not task_arn:
        return {"ran": False,
                "skipped_reason": "no DataSync task ARN given "
                "(DATASYNC_TASK_ARN / --datasync-task-arn)"}
    client = client or boto3.client(
        "datasync", region_name=region,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"))
    started = client.start_task_execution(TaskArn=task_arn)
    exec_arn = started["TaskExecutionArn"]
    final = wait_task_execution(client, exec_arn)
    result = final.get("Result", {})
    transferred = {
        "files_transferred": final.get("FilesTransferred"),
        "bytes_transferred": final.get("BytesTransferred"),
        "bytes_written": final.get("BytesWritten"),
        "verify_status": result.get("VerifyStatus"),
        "transfer_status": result.get("TransferStatus"),
        "prepare_status": result.get("PrepareStatus"),
        "error_code": result.get("ErrorCode"),
        "error_detail": result.get("ErrorDetail"),
    }
    # Independent correctness check: the engine's verify pass over
    # the DataSync target bucket (the finance share bucket).
    target = f"{bucket_prefix}-finance"
    v = verify(s3, source / "finance", target)
    checked = v.checked
    mismatched = len(v.mismatched) + len(v.missing)
    all_pass = v.passed
    return {"ran": True, "shares": ["finance"],
            "objects": transferred["files_transferred"],
            "bytes": transferred["bytes_transferred"],
            "seconds": None,
            "mb_s": None,
            "verify": {"checked": checked, "mismatched": mismatched,
                       "passed": all_pass},
            "verification": "DataSync task verification "
                            "(POINT_IN_TIME_CONSISTENT, as reported by "
                            "the task execution) + engine verify pass "
                            "over the target bucket",
            "datasync_execution": transferred,
            "notes": ["DataSync covers the finance share only; a full "
                      "four-share comparison needs four tasks."]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="Dataset root (share subdirectories)")
    ap.add_argument("--bucket-prefix", default="nas2aws-migration")
    ap.add_argument("--methods", default="engine,s3sync",
                    help="Comma-separated: engine,s3sync,datasync")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--datasync-task-arn", default=None,
                    help="DataSync task ARN; default from DATASYNC_TASK_ARN")
    ap.add_argument("--out", default="evidence/comparison.json")
    args = ap.parse_args()

    source = Path(args.source).expanduser()
    if not source.is_dir():
        print(f"source is not a directory: {source}", file=sys.stderr)
        return 2
    region = os.environ.get("AWS_REGION", "us-west-2")
    endpoint = os.environ.get("S3_ENDPOINT") or None
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    unknown = [m for m in methods if m not in ("engine", "s3sync", "datasync")]
    if unknown:
        print(f"unknown methods: {', '.join(unknown)} "
              f"(engine, s3sync, datasync)", file=sys.stderr)
        return 2

    s3 = garage_client()

    def client_factory():
        return boto3.client(
            "s3", region_name=region, endpoint_url=endpoint,
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
            config=Config(s3={"addressing_style": "path"}))

    evidence: dict = {
        "backend": backend_label("Garage (local S3 emulator)"),
        "dataset": dataset_stats(source),
        "methods": {}, "ran": [], "skipped": {}}

    if "engine" in methods:
        rec = run_engine_method(s3, source, args.bucket_prefix, region,
                                args.workers, client_factory)
        evidence["methods"]["engine"] = rec
        if rec["ran"]:
            evidence["ran"].append("engine")
        else:
            evidence["skipped"]["engine"] = rec.get("skipped_reason")
        print(f"engine: ran={rec['ran']} "
              f"{rec.get('mb_s', '')} MB/s verify={rec.get('verify', {})}")
    if "s3sync" in methods:
        rec = run_s3sync_method(source, args.bucket_prefix, endpoint,
                                region, s3)
        evidence["methods"]["s3sync"] = rec
        if rec["ran"]:
            evidence["ran"].append("s3sync")
        else:
            evidence["skipped"]["s3sync"] = rec.get("skipped_reason")
        print(f"s3sync: ran={rec['ran']} "
              f"{rec.get('mb_s', '')} MB/s verify={rec.get('verify', {})}")
    if "datasync" in methods:
        rec = run_datasync_method(
            args.datasync_task_arn or os.environ.get("DATASYNC_TASK_ARN"),
            s3, source, args.bucket_prefix, region)
        evidence["methods"]["datasync"] = rec
        if rec["ran"]:
            evidence["ran"].append("datasync")
        else:
            evidence["skipped"]["datasync"] = rec.get("skipped_reason")
        print(f"datasync: ran={rec['ran']} "
              f"reason={rec.get('skipped_reason', '')}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2))
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
