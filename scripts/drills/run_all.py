#!/usr/bin/env python3
"""Run all six failure drills + the ransomware drill, 3 runs each,
take medians, and write the evidence file the failure matrix is
built from.

Usage (Garage drills need creds via environment):
  bash ~/workspace/nas2aws-ceiling/run-with-garage.sh \
      python scripts/drills/run_all.py \
      --out evidence/failure-matrix-2026-11-24.json
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import drill_corruption
import drill_creds
import drill_dns
import drill_kms
import drill_multipart
import drill_throttle
import ransomware
from common import run_n, write_evidence

DRILLS = [
    ("expired_credentials", drill_creds.run_once),
    ("kms_denied", drill_kms.run_once),
    ("dns_endpoint_failure", drill_dns.run_once),
    ("throttling", drill_throttle.run_once),
    ("interrupted_multipart", drill_multipart.run_once),
    ("silent_corruption", drill_corruption.run_once),
    ("ransomware", ransomware.run_once),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    date = datetime.date.today().isoformat()
    out = args.out or f"evidence/failure-matrix-{date}.json"

    results = {"date": date, "backend_notes": {
        "Garage v1.3.0": "local disk-backed S3 emulator; no KMS, no versioning, no Object Lock",
        "moto 5.2.5": "in-process mock; versioning works, KMS key state and Object Lock not enforced",
        "real AWS": "genuine KMS denial, throttling, and Object Lock enforcement are real-AWS-only",
    }, "drills": []}
    ok = True
    for name, fn in DRILLS:
        rec = run_n(name, fn, n=args.runs)
        results["drills"].append(rec)
        good = rec["detected_runs"] == args.runs and rec["recovered_runs"] == args.runs
        ok = ok and good
        print(f"{name}: detected {rec['detected_runs']}/{args.runs}, "
              f"recovered {rec['recovered_runs']}/{args.runs}, "
              f"median detect {rec['median_detect_seconds']}s, "
              f"median recover {rec['median_recover_seconds']}s")
    print(f"saved {write_evidence(out, results)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
