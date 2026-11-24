#!/usr/bin/env python3
"""Run all six Azure Blob failure drills, 3 runs each, take medians,
and write the evidence file the Azure failure matrix is built from.

Usage (Azurite must be running):
  bash scripts/start_azurite.sh
  ~/workspace/nas-to-aws-migration-venv-azure/bin/python scripts/azure/drills/run_all.py \
      --out evidence/azure-failure-matrix-2026-11-24.json
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

import drill_corruption
import drill_dns
import drill_interrupted
import drill_key_denied
import drill_sas_expired
import drill_throttle
from common import run_n, write_evidence

DRILLS = [
    ("expired_sas", drill_sas_expired.run_once),
    ("key_denied", drill_key_denied.run_once),
    ("dns_endpoint_failure", drill_dns.run_once),
    ("throttling", drill_throttle.run_once),
    ("interrupted_upload", drill_interrupted.run_once),
    ("silent_corruption", drill_corruption.run_once),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    date = datetime.date.today().isoformat()
    out = args.out or f"evidence/azure-failure-matrix-{date}.json"

    results = {"date": date, "backend_notes": {
        "Azurite 3.38.0": "local Blob emulator; versioning via --accountConfigAsJson flag; "
                          "soft delete and immutability return 501; never throttles",
        "real Azure": "soft-delete restore, immutability/legal hold, genuine "
                      "throttling (429/503), Entra/RBAC auth, and Terraform "
                      "apply are real-Azure-only",
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
