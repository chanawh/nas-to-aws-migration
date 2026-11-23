#!/usr/bin/env python3
"""Scaled migration run against any S3-compatible endpoint.

Generates the base 24-file dataset plus N 1 MB scale files per
share (default 2500/share = 10,000 files, ~10 GB), then runs the
full cycle with the real sync engine: sync, verify, corrupt 10
objects, verify (must FAIL with exactly 10 mismatches), repair
sync, verify (must PASS). Prints timings and writes a JSON
evidence file.

Why a disk-backed backend: moto keeps objects in RAM, so a
10 GB run does not fit a small VM. The recorded 2026-11-22 run
used Garage v1.3.0 (local, disk-backed, S3-compatible) - see
docs/SCALED_RUN.md. Any S3 endpoint works the same way.

Usage (credentials via environment, never committed):
  S3_ENDPOINT=http://127.0.0.1:3900 \
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  python scripts/run_scaled_demo.py --source /path/to/source \
      --per-share 2500 --file-mb 1 \
      --out evidence/scaled-run.json

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--per-share", type=int, default=2500)
    ap.add_argument("--file-mb", type=int, default=1)
    ap.add_argument("--bucket-prefix", default="nas2aws-migration")
    ap.add_argument("--out", default="evidence/scaled-run.json")
    args = ap.parse_args()

    src = Path(args.source)
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
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        config=Config(s3={"addressing_style": "path"}))
    for s in SHARES:
        try:
            s3.create_bucket(Bucket=f"{args.bucket_prefix}-{s}")
        except Exception as e:
            log(f"bucket {s}: {e}")
        objs = s3.list_objects_v2(Bucket=f"{args.bucket_prefix}-{s}").get("Contents", [])
        for o in objs:
            s3.delete_object(Bucket=f"{args.bucket_prefix}-{s}", Key=o["Key"])
    log("buckets ready and emptied")

    quotas = load_quotas(src / "quotas.json")
    acl_map = load_acl(src / "acl.json")
    results = {"backend": os.environ.get("S3_BACKEND_LABEL", "S3-compatible emulator (local)")}

    def timed(label, fn):
        t0 = time.time()
        out = fn()
        results[label] = round(time.time() - t0, 2)
        log(f"{label}: {results[label]:.2f}s")
        return out

    def do_sync(tag):
        r = sync_all(s3, src, args.bucket_prefix, quotas=quotas, acl_map=acl_map)
        copied = sum(len(x.copied) for x in r.values())
        b = sum(x.bytes_copied for x in r.values())
        unchanged = sum(len(x.skipped_unchanged) for x in r.values())
        log(f"{tag} copied={copied} bytes={b} unchanged={unchanged}")
        results[tag] = {"copied": copied, "bytes": b, "unchanged": unchanged}
        return r

    def do_verify(label):
        checked = mismatched = 0
        all_pass = True
        for share_dir in sorted(p for p in src.iterdir() if p.is_dir()):
            v = verify(s3, share_dir, f"{args.bucket_prefix}-{share_dir.name}")
            checked += v.checked
            mismatched += len(v.mismatched)
            all_pass = all_pass and v.passed
            log(f"{label} {share_dir.name}: passed={v.passed} "
                f"checked={v.checked} mismatched={len(v.mismatched)}")
        results[label] = {"checked": checked, "mismatched": mismatched, "passed": all_pass}
        return all_pass

    timed("sync_seconds", lambda: do_sync("initial_sync"))
    timed("verify1_seconds", lambda: do_verify("verify1"))

    log("corrupting 10 objects")
    n = 0
    for s in SHARES:
        for i in range(3 if s in ("finance", "it-support") else 2):
            s3.put_object(Bucket=f"{args.bucket_prefix}-{s}",
                          Key=f"scale-{i:05d}.bin", Body=b"corrupted\n")
            n += 1
    results["corruptions_injected"] = n

    timed("verify2_seconds", lambda: do_verify("verify2"))
    timed("repair_seconds", lambda: do_sync("repair_sync"))
    timed("verify3_seconds", lambda: do_verify("verify3"))

    results["dataset_bin_bytes"] = sum(
        f.stat().st_size for f in src.rglob("*") if f.is_file() and f.suffix == ".bin")
    results["throughput_mb_s"] = round(
        results["initial_sync"]["bytes"] / results["sync_seconds"] / 1e6, 2)
    results["objects_per_s"] = round(
        results["initial_sync"]["copied"] / results["sync_seconds"], 1)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    log(f"RESULTS {json.dumps(results)}")
    log(f"saved {out}")
    ok = (results["verify1"]["passed"] and not results["verify2"]["passed"]
          and results["verify2"]["mismatched"] == n and results["verify3"]["passed"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
