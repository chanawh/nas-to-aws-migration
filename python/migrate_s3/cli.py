"""CLI: generate | sync | verify | report.

Endpoint is the one flag that matters:
  LocalStack: --endpoint-url http://localhost:4566 (default)
  moto:       --endpoint-url http://localhost:5000
  Real AWS:   --endpoint-url ""  (omit the flag value / pass empty)

Examples (run from python/, with the emulator running):
    python -m migrate_s3.cli generate --root /tmp/nas2aws-demo/source
    python -m migrate_s3.cli sync --source /tmp/nas2aws-demo/source --endpoint-url http://localhost:5000
    python -m migrate_s3.cli verify --source /tmp/nas2aws-demo/source --endpoint-url http://localhost:5000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .acl import load_acl, validate_acl
from .audit import make_dynamodb_client, make_sns_client, publish_verify_failure, write_audit
from .dataset import generate_dataset
from .onefs import (
    SHARES as ONEFS_SHARES,
    check_onefs,
    mount_shares,
    seed_onefs,
    unmount_shares,
    validate_mount_root,
)
from .quotas import check_quotas, load_quotas
from .sync import make_s3_client, sync_all
from .verify import verify

import os as _os


def _onefs_creds(args):
    user = getattr(args, "onefs_user", None) or _os.environ.get("ONEFS_USER", "root")
    password = getattr(args, "onefs_password", None) or _os.environ.get("ONEFS_PASSWORD", "")
    return user, password

DEFAULT_BUCKET_PREFIX = "nas2aws-migration"
DEFAULT_ENDPOINT = "http://localhost:4566"


def _s3(args):
    endpoint = args.endpoint_url or None
    return make_s3_client(endpoint, region=args.region)


def _cmd_generate(args) -> int:
    root = Path(args.root)
    try:
        summary = generate_dataset(root, seed=args.seed)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"generated {summary['files']} files, {summary['bytes']} bytes, shares: {', '.join(summary['shares'])}")
    print(f"source: {root}")
    return 0


def _check_onefs_before(args) -> int:
    """If --onefs-host was given, fail fast when the node is unreachable."""
    host = getattr(args, "onefs_host", None)
    if not host:
        return 0
    user, password = _onefs_creds(args)
    result = check_onefs(host, user, password)
    if not result.ok:
        print(f"error: OneFS node {host} is not ready:", file=sys.stderr)
        for e in result.errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"OneFS {host} ok: SMB 445 open, API 8080 open" + (f", cluster {result.cluster_name}" if result.cluster_name else ""))
    return 0


def _cmd_check_onefs(args) -> int:
    user, password = _onefs_creds(args)
    result = check_onefs(args.onefs_host, user, password)
    print(f"host: {result.host}")
    print(f"reachable: {result.reachable}")
    print(f"SMB 445: {'open' if result.tcp_445_smb else 'closed'}")
    print(f"Platform API 8080: {'open' if result.tcp_8080_api else 'closed'}")
    print(f"SSH 22: {'open' if result.tcp_22_ssh else 'closed'}")
    if result.cluster_name:
        print(f"cluster: {result.cluster_name}")
    if result.onefs_version:
        print(f"OneFS: {result.onefs_version}")
    if result.errors:
        for e in result.errors:
            print(f"problem: {e}")
    print("check-onefs: PASS" if result.ok else "check-onefs: FAIL")
    return 0 if result.ok else 1


def _cmd_mount_onefs(args) -> int:
    if _check_onefs_before(args):
        return 1
    user, password = _onefs_creds(args)
    mount_root = Path(args.mount_root)
    try:
        ran = mount_shares(args.onefs_host, mount_root, user, password, dry_run=args.dry_run)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for cmd in ran:
        print(f"  {cmd}")
    print(f"mounted {', '.join(ONEFS_SHARES)} under {mount_root}" + (" [dry-run]" if args.dry_run else ""))
    return 0


def _cmd_unmount_onefs(args) -> int:
    ran = unmount_shares(Path(args.mount_root), dry_run=args.dry_run)
    for cmd in ran:
        print(f"  {cmd}")
    print("unmounted")
    return 0


def _cmd_seed_onefs(args) -> int:
    source = Path(args.source)
    mount_root = Path(args.mount_root)
    problems = validate_mount_root(mount_root)
    # seed-onefs is allowed to create the mount tree, but the shares
    # themselves must already be mounted - otherwise it would copy
    # files onto the host disk and pretend they were on OneFS.
    share_problems = [p for p in problems if "is missing" in p and "share directory" in p]
    if share_problems:
        print("error: shares are not mounted:", file=sys.stderr)
        for p in share_problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    try:
        copied = seed_onefs(source, mount_root)
    except (FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for share, count in copied.items():
        print(f"{share}: seeded {count} files onto OneFS")
    print(f"total: {sum(copied.values())} files")
    return 0


def _cmd_sync(args) -> int:
    source = Path(args.source)
    if _check_onefs_before(args):
        return 1
    if getattr(args, "source_mode", "local") == "onefs":
        problems = validate_mount_root(source)
        if problems:
            print("error: OneFS mount root is not ready:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
    if not source.is_dir():
        print(f"error: source {source} is not a directory", file=sys.stderr)
        return 1
    problems = validate_acl(source / "users.csv", source / "acl.json")
    if problems:
        print("ACL pre-flight failed:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    s3 = _s3(args)
    quotas = load_quotas(source / "quotas.json")
    acl_map = load_acl(source / "acl.json")
    try:
        results = sync_all(
            s3, source, args.bucket_prefix,
            dry_run=args.dry_run, delete=args.delete,
            quotas=quotas, acl_map=acl_map,
        )
    except Exception as e:
        print(f"error: sync failed: {e}", file=sys.stderr)
        print("Is the emulator running, and did Terraform apply create the buckets?", file=sys.stderr)
        return 1
    total_copied = sum(len(r.copied) for r in results.values())
    total_bytes = sum(r.bytes_copied for r in results.values())
    total_unchanged = sum(len(r.skipped_unchanged) for r in results.values())
    if args.dynamodb_table and not args.dry_run:
        ddb = make_dynamodb_client(args.endpoint_url or None, region=args.region)
        audited = 0
        for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
            audited += write_audit(ddb, args.dynamodb_table, share_dir.name, share_dir)
        print(f"audit: wrote {audited} records to DynamoDB table {args.dynamodb_table}")
    for share, r in results.items():
        if r.skipped_quota:
            print(f"{share}: SKIPPED - over quota ({len(r.skipped_quota)} files not uploaded)")
        else:
            print(f"{share}: uploaded {len(r.copied)}, unchanged {len(r.skipped_unchanged)}, deleted {len(r.deleted)}, {r.bytes_copied} bytes")
    print(f"total: uploaded {total_copied} files ({total_bytes} bytes), unchanged {total_unchanged}" + (" [dry-run]" if args.dry_run else ""))
    return 0


def _cmd_verify(args) -> int:
    source = Path(args.source)
    if _check_onefs_before(args):
        return 1
    s3 = _s3(args)
    overall_ok = True
    for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        result = verify(s3, share_dir, f"{args.bucket_prefix}-{share_dir.name}")
        status = "PASS" if result.passed else "FAIL"
        print(f"{share_dir.name}: {status} - checked {result.checked}, ok {len(result.ok)}, missing {len(result.missing)}, mismatched {len(result.mismatched)}, extra {len(result.extra)}")
        if not result.passed:
            overall_ok = False
            for rel in result.failed:
                print(f"  - failed: {rel}")
            if args.sns_topic_arn:
                sns = make_sns_client(args.endpoint_url or None, region=args.region)
                publish_verify_failure(sns, args.sns_topic_arn, share_dir.name, result.failed)
                print(f"  alert: published verify FAIL to SNS {args.sns_topic_arn}")
    print("verify: PASS" if overall_ok else "verify: FAIL")
    return 0 if overall_ok else 1


def _cmd_report(args) -> int:
    source = Path(args.source)
    s3 = _s3(args)
    quotas = load_quotas(source / "quotas.json")
    report = {"source": str(source), "endpoint": args.endpoint_url, "quotas": check_quotas(source, quotas), "shares": {}}
    for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        result = verify(s3, share_dir, f"{args.bucket_prefix}-{share_dir.name}")
        report["shares"][share_dir.name] = {
            "checked": result.checked, "ok": len(result.ok),
            "missing": len(result.missing), "mismatched": len(result.mismatched),
            "extra": len(result.extra), "passed": result.passed,
        }
    print(json.dumps(report, indent=2))
    return 0


def _add_common(p):
    p.add_argument("--endpoint-url", default=DEFAULT_ENDPOINT, help="Emulator URL, or empty for real AWS")
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--bucket-prefix", default=DEFAULT_BUCKET_PREFIX)
    p.add_argument("--dynamodb-table", default="", help="DynamoDB audit table, e.g. nas2aws-migration-audit. Empty means no audit writes.")
    p.add_argument("--sns-topic-arn", default="", help="SNS topic ARN for verify-failure alerts. Empty means no alerts.")


def _add_source(p):
    """Source flags. --source-mode onefs validates the mount root and,
    with --onefs-host, checks the node is reachable before syncing."""
    p.add_argument("--source-mode", choices=["local", "onefs"], default="local")
    p.add_argument("--onefs-host", default="", help="OneFS virtual node IP, e.g. 192.168.1.50. Empty means no OneFS check.")
    p.add_argument("--onefs-user", default="", help="OneFS user, default root or $ONEFS_USER")
    p.add_argument("--onefs-password", default="", help="OneFS password, default $ONEFS_PASSWORD. Never commit it.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="migrate_s3")
    sub = parser.add_subparsers(dest="command", required=True)
    p_gen = sub.add_parser("generate", help="Create the synthetic source NAS dataset")
    p_gen.add_argument("--root", required=True)
    p_gen.add_argument("--seed", type=int, default=7)
    p_gen.set_defaults(func=_cmd_generate)
    p_sync = sub.add_parser("sync", help="Sync source to S3 with checksum comparison")
    p_sync.add_argument("--source", required=True)
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.add_argument("--delete", action="store_true")
    _add_common(p_sync)
    _add_source(p_sync)
    p_sync.set_defaults(func=_cmd_sync)
    p_ver = sub.add_parser("verify", help="Download every object and re-hash it")
    p_ver.add_argument("--source", required=True)
    _add_common(p_ver)
    _add_source(p_ver)
    p_ver.set_defaults(func=_cmd_verify)
    p_check = sub.add_parser("check-onefs", help="Check a OneFS virtual node is reachable (SMB 445, API 8080, SSH 22)")
    p_check.add_argument("--onefs-host", required=True)
    p_check.add_argument("--onefs-user", default="")
    p_check.add_argument("--onefs-password", default="")
    p_check.set_defaults(func=_cmd_check_onefs)
    p_mount = sub.add_parser("mount-onefs", help="Mount the 4 OneFS SMB shares under a mount root")
    p_mount.add_argument("--onefs-host", required=True)
    p_mount.add_argument("--mount-root", required=True)
    p_mount.add_argument("--onefs-user", default="")
    p_mount.add_argument("--onefs-password", default="")
    p_mount.add_argument("--dry-run", action="store_true")
    p_mount.set_defaults(func=_cmd_mount_onefs)
    p_unmount = sub.add_parser("unmount-onefs", help="Unmount the OneFS shares")
    p_unmount.add_argument("--mount-root", required=True)
    p_unmount.add_argument("--dry-run", action="store_true")
    p_unmount.set_defaults(func=_cmd_unmount_onefs)
    p_seed = sub.add_parser("seed-onefs", help="Copy a generated dataset into the mounted OneFS shares")
    p_seed.add_argument("--source", required=True, help="Local dataset from generate")
    p_seed.add_argument("--mount-root", required=True)
    p_seed.set_defaults(func=_cmd_seed_onefs)
    p_rep = sub.add_parser("report", help="JSON report: quotas + per-share verify")
    p_rep.add_argument("--source", required=True)
    _add_common(p_rep)
    p_rep.set_defaults(func=_cmd_report)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
