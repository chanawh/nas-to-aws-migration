#!/usr/bin/env python3
"""Copy the dataset to an EFS mount with SHA-256 verification.

The engine writes to S3; this is the secondary target for the
workloads that need a POSIX file system and cannot go to object
storage. It reuses the engine's hashing (scan_directory /
compute_sha256 from migrate_s3.sync) and the same manifest shape
(_manifest.json per share), so the EFS copy is verified the same
way the S3 copy is: re-hash the actual bytes, never trust the copy.

The mount must already exist (an EFS file system mounted with
`mount -t nfs4 -o nfsvers=4.1 <dns>:/ <mount-point>`, DNS name
from `terraform output efs_dns_name`). This script does not mount
anything; it copies into an already-mounted path.

EFS is real-AWS-only and cannot be emulated. Against a local
directory the script still runs and verifies, which is how the
copy/verify logic is unit-tested -- but the evidence then says
"local directory stand-in (NOT EFS)", never EFS. The backend
label comes from /proc/mounts: a mount whose source looks like
<fs-id>.efs.<region>.amazonaws.com is a real EFS mount.

Quotas: the same per-share byte caps from quotas.json are
checked before a share is copied. An over-quota share is skipped
entirely and reported, never half-copied -- the same rule as the
S3 engine.

Usage (no AWS credentials needed; the mount is a local path):
  python scripts/migrate_efs.py --source <dataset> --mount /mnt/efs \
      --out evidence/efs-migration-2026-11-25.json
  python scripts/migrate_efs.py --source <dataset> --mount /mnt/efs \
      --verify-only --out evidence/efs-verify-2026-11-25.json
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from migrate_s3.quotas import load_quotas
from migrate_s3.sync import MANIFEST_KEY, compute_sha256, scan_directory

SHARES = ["finance", "it-support", "it-operations", "operations"]


def is_efs_mount(mount: Path, mounts_file: Path = Path("/proc/mounts")) -> bool:
    """True when the mount path is a real EFS mount. Best effort:
    /proc/mounts lists the mount source; an EFS mount shows
    <fs-id>.efs.<region>.amazonaws.com:/. A local directory (or a
    missing mounts file) is not EFS."""
    try:
        lines = mounts_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    mount_str = str(mount.resolve())
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        source, target = parts[0], parts[1]
        if target == mount_str and ".efs." in source and "amazonaws.com" in source:
            return True
    return False


def copy_share(source: Path, mount: Path) -> dict:
    """Copy one share to the mount, writing a manifest with the
    SHA-256 per file. Returns {copied, bytes, files}."""
    records = scan_directory(source)
    dest = mount / source.name
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    total = 0
    for rel in sorted(records):
        src_file = source / rel
        dst_file = dest / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        copied.append(rel)
        total += records[rel].size
    manifest = {
        "version": 1,
        "files": {
            rel: {"size": r.size, "sha256": r.sha256, "mtime": r.mtime}
            for rel, r in records.items()
        },
    }
    (dest / MANIFEST_KEY).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {"copied": copied, "bytes": total,
            "files": {rel: records[rel].sha256 for rel in copied}}


def verify_mount(source: Path, mount: Path) -> dict:
    """Re-hash every file on the mount against the source scan.
    The manifest itself is excluded (it is not in the source).
    Returns {checked, ok, missing, mismatched, extra, passed}."""
    src = scan_directory(source)
    dst_dir = mount / source.name
    dst = {rel: rec for rel, rec in scan_directory(dst_dir).items()
           if rel != MANIFEST_KEY}
    ok, missing, mismatched = [], [], []
    for rel, rec in src.items():
        if rel not in dst:
            missing.append(rel)
        elif dst[rel].sha256 != rec.sha256:
            mismatched.append(rel)
        else:
            ok.append(rel)
    extra = sorted(rel for rel in dst if rel not in src)
    return {"checked": len(src), "ok": ok, "missing": missing,
            "mismatched": mismatched, "extra": extra,
            "passed": not missing and not mismatched}


def migrate_efs(source_root: Path, mount: Path,
                quotas: dict[str, int] | None = None) -> dict:
    """Copy every share to the mount (quota-checked), then verify.
    Returns the evidence dict; nothing in it is a modelled number."""
    quotas = quotas or {}
    backend = ("EFS (real AWS)" if is_efs_mount(mount)
               else "local directory stand-in (NOT EFS)")
    result: dict = {"backend": backend,
                    "source": str(source_root), "mount": str(mount),
                    "shares": {}, "verify": {}}
    for share_dir in sorted(p for p in source_root.iterdir() if p.is_dir()):
        share = share_dir.name
        total = sum(p.stat().st_size for p in share_dir.rglob("*")
                    if p.is_file())
        quota = quotas.get(share)
        if quota is not None and total > quota:
            result["shares"][share] = {
                "copied": [], "bytes": 0, "skipped_quota": True,
                "quota": quota, "total_bytes": total}
            continue
        result["shares"][share] = copy_share(share_dir, mount)
        result["shares"][share]["skipped_quota"] = False
    # Verify every share that was copied.
    checked = mismatched = 0
    all_pass = True
    for share_dir in sorted(p for p in source_root.iterdir() if p.is_dir()):
        if result["shares"][share_dir.name].get("skipped_quota"):
            continue
        v = verify_mount(share_dir, mount)
        result["verify"][share_dir.name] = v
        checked += v["checked"]
        mismatched += len(v["mismatched"]) + len(v["missing"])
        all_pass = all_pass and v["passed"]
    result["verify_totals"] = {"checked": checked,
                               "mismatched": mismatched,
                               "passed": all_pass}
    result["status"] = ("measured" if all_pass and
                        not any(s.get("skipped_quota")
                                for s in result["shares"].values())
                        else "not_measured")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="Dataset root (share subdirectories)")
    ap.add_argument("--mount", required=True,
                    help="EFS mount point (already mounted)")
    ap.add_argument("--quotas", default=None,
                    help="quotas.json path; default <source>/quotas.json")
    ap.add_argument("--verify-only", action="store_true",
                    help="Only verify the mount against the source")
    ap.add_argument("--out", default="evidence/efs-migration.json")
    args = ap.parse_args()

    source = Path(args.source).expanduser()
    mount = Path(args.mount).expanduser()
    if not source.is_dir():
        print(f"source is not a directory: {source}", file=sys.stderr)
        return 2
    if not mount.is_dir():
        print(f"mount is not a directory (is the EFS file system "
              f"mounted?): {mount}", file=sys.stderr)
        return 2

    quotas = load_quotas(Path(args.quotas) if args.quotas
                         else source / "quotas.json")
    if args.verify_only:
        checked = mismatched = 0
        all_pass = True
        verify = {}
        for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
            v = verify_mount(share_dir, mount)
            verify[share_dir.name] = v
            checked += v["checked"]
            mismatched += len(v["mismatched"]) + len(v["missing"])
            all_pass = all_pass and v["passed"]
        evidence = {
            "backend": ("EFS (real AWS)" if is_efs_mount(mount)
                        else "local directory stand-in (NOT EFS)"),
            "source": str(source), "mount": str(mount),
            "verify": verify,
            "verify_totals": {"checked": checked,
                              "mismatched": mismatched,
                              "passed": all_pass},
            "status": "measured" if all_pass else "not_measured"}
    else:
        evidence = migrate_efs(source, mount, quotas)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2))
    print(f"backend: {evidence['backend']}")
    print(f"verify: passed={evidence['verify_totals']['passed']} "
          f"checked={evidence['verify_totals']['checked']} "
          f"mismatched={evidence['verify_totals']['mismatched']}")
    print(f"saved {out}")
    return 0 if evidence["status"] == "measured" else 1


if __name__ == "__main__":
    raise SystemExit(main())
