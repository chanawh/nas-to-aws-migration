#!/usr/bin/env python3
"""Throughput benchmark: 1 worker vs N workers, same dataset, same backend.

Generates the base 24-file dataset plus N 1 MB scale files per
share, then runs the real sync engine with different worker
counts, emptying the buckets between runs so every run is a
full upload. Writes a JSON evidence file with per-run timings,
medians, and speedup.

Why this exists: the single-threaded engine measured 13.5 MB/s
on the 10 GB run (evidence/scaled-run-10gb-2026-11-22.json).
At that rate a 1 TB run takes ~20 hours, so parallelism is the
prerequisite for scale. This script measures the actual speedup.

Usage (credentials via environment, never committed):
  S3_ENDPOINT=http://127.0.0.1:3900 \
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  S3_BACKEND_LABEL="Garage v1.3.0 (local disk-backed emulator)" \
  python scripts/run_throughput.py --source ~/workspace/nas2aws-throughput/source \
      --per-share 100 --file-mb 1 --workers 1 1 4 8 8 \
      --out evidence/throughput-2026-11-24.json

Buckets named <bucket-prefix>-<share> must exist (or be
creatable) at the endpoint before the run.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import boto3
from botocore.config import Config

from migrate_s3 import dataset
from migrate_s3.acl import load_acl
from migrate_s3.quotas import load_quotas
from migrate_s3.sync import sync_all
from migrate_s3.verify import verify

SHARES = ["finance", "it-support", "it-operations", "operations"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def empty_bucket(s3, bucket):
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=o["Key"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--per-share", type=int, default=100)
    ap.add_argument("--file-mb", type=int, default=1)
    ap.add_argument("--bucket-prefix", default="nas2aws-migration")
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 1, 4, 8, 8],
                    help="Worker counts, one timed run each, in order")
    ap.add_argument("--out", default="evidence/throughput.json")
    args = ap.parse_args()

    src = Path(args.source).expanduser()
    file_bytes = args.file_mb * 1024 * 1024
    if not src.exists():
        dataset.generate_dataset(src, seed=7)
        total = 0
        for s in SHARES:
            for i in range(args.per_share):
                block = hashlib.sha256(f"{s}-{i}".encode()).digest()
                data = (block * (file_bytes // len(block) + 1))[:file_bytes]
                (src / s / f"scale-{i:05d}.bin").write_bytes(data)
                total += len(data)
        (src / "quotas.json").write_text(
            json.dumps({s: 10**13 for s in SHARES}, indent=2))
        log(f"scale files written: {args.per_share * 4} files, {total} bytes")
    else:
        log("source exists, reusing")

    s3 = boto3.client(
        "s3", region_name=os.environ.get("AWS_REGION", "us-west-2"),
        endpoint_url=os.environ.get("S3_ENDPOINT") or None,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        config=Config(s3={"addressing_style": "path"}))

    def client_factory():
        return boto3.client(
            "s3", region_name=os.environ.get("AWS_REGION", "us-west-2"),
            endpoint_url=os.environ.get("S3_ENDPOINT") or None,
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            config=Config(s3={"addressing_style": "path"}))

    quotas = load_quotas(src / "quotas.json")
    acl_map = load_acl(src / "acl.json")
    results = {
        "backend": os.environ.get("S3_BACKEND_LABEL", "S3-compatible emulator (local)"),
        "dataset": {
            "files": args.per_share * 4 + 24,
            "per_share": args.per_share,
            "file_mb": args.file_mb,
        },
        "runs": [],
    }
    # Real dataset bytes: count every file except the control files.
    results["dataset"]["bytes"] = sum(
        f.stat().st_size for f in src.rglob("*")
        if f.is_file() and f.name not in ("quotas.json", "acl.json", "users.csv"))

    for workers in args.workers:
        for s in SHARES:
            try:
                s3.create_bucket(Bucket=f"{args.bucket_prefix}-{s}")
            except Exception:
                pass
            empty_bucket(s3, f"{args.bucket_prefix}-{s}")
        t0 = time.time()
        r = sync_all(s3, src, args.bucket_prefix, quotas=quotas,
                     acl_map=acl_map, workers=workers,
                     client_factory=client_factory)
        elapsed = round(time.time() - t0, 2)
        copied = sum(len(x.copied) for x in r.values())
        b = sum(x.bytes_copied for x in r.values())
        run = {"workers": workers, "seconds": elapsed, "copied": copied,
               "bytes": b, "mb_s": round(b / elapsed / 1e6, 2),
               "objects_per_s": round(copied / elapsed, 1)}
        results["runs"].append(run)
        log(f"workers={workers}: {elapsed:.2f}s {run['mb_s']} MB/s "
            f"{run['objects_per_s']} obj/s copied={copied}")

    # Correctness: full verify after the last run.
    checked = mismatched = 0
    all_pass = True
    for share_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        v = verify(s3, share_dir, f"{args.bucket_prefix}-{share_dir.name}")
        checked += v.checked
        mismatched += len(v.mismatched)
        all_pass = all_pass and v.passed
    results["verify_after_last_run"] = {
        "checked": checked, "mismatched": mismatched, "passed": all_pass}
    log(f"verify: passed={all_pass} checked={checked} mismatched={mismatched}")

    def median(vals):
        vals = sorted(vals)
        n = len(vals)
        return vals[n // 2] if n % 2 else round((vals[n // 2 - 1] + vals[n // 2]) / 2, 2)

    by_workers = {}
    for run in results["runs"]:
        by_workers.setdefault(run["workers"], []).append(run["mb_s"])
    results["median_mb_s_by_workers"] = {
        str(w): median(v) for w, v in sorted(by_workers.items())}
    base = results["median_mb_s_by_workers"].get("1")
    if base and len(by_workers) > 1:
        best = max(results["median_mb_s_by_workers"].items(),
                   key=lambda kv: kv[1])
        results["speedup_vs_1_worker"] = {
            "workers": int(best[0]), "median_mb_s": best[1],
            "factor": round(best[1] / base, 2)}
        log(f"speedup: {best[1]} MB/s at {best[0]} workers "
            f"= {results['speedup_vs_1_worker']['factor']}x vs 1 worker")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    log(f"saved {out}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
