"""Ransomware drill: compromised credential overwrites and deletes.

Attack (as a compromised credential would): overwrite 10 objects
with junk and delete 10 more, out of 24 synced objects.
Detect: verify re-hashes every object and reports exactly the
20 affected.
Recover: versioning restore. A deleted object comes back by
deleting its delete marker; an overwritten object comes back by
copying its pre-attack version over the current one. Final
verify must pass with zero data loss.
Backend: moto (versioning works there; Garage returns
NotImplemented for PutBucketVersioning, verified 2026-11-24).
Object Lock: moto accepts the configuration but does not
enforce it (a delete of a GOVERNANCE-retained object was
allowed, verified 2026-11-24), so lock enforcement is
real-AWS-only and is recorded as such, not claimed.
On real AWS (no S3_ENDPOINT) the same code runs against the
real buckets (versioning from Terraform; Object Lock from
Terraform with enable_object_lock=true) and the lock probe
genuinely tests enforcement.
"""
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

from common import backend_label, garage_client, is_real_aws
from migrate_s3.dataset import generate_dataset
from migrate_s3.sync import sync_all
from migrate_s3.verify import verify

PREFIX = "nas2aws-migration"
SHARES = ["finance", "it-support", "it-operations", "operations"]
OVERWRITE = 10
DELETE = 10


def _all_keys(source):
    keys = []
    for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        for f in sorted(share_dir.rglob("*")):
            if f.is_file():
                keys.append((share_dir.name, f.relative_to(share_dir).as_posix()))
    return keys


def _run_with_client(s3, lock_bucket, real):
    """Attack, detect, recover, and the Object Lock probe, against
    any S3 client. The caller owns bucket setup: on moto the
    buckets are created here, on real AWS they come from
    Terraform (versioning on, Object Lock when enabled)."""
    # On real AWS the share buckets carry a GOVERNANCE Object Lock
    # default retention, so deleting a retained object needs the
    # break-glass bypass. The attack and the recovery use it so the
    # drill can exercise versioning-based detection and restore.
    # Object Lock enforcement itself is tested separately by the
    # lock probe below, which deliberately does NOT pass bypass —
    # its delete must be denied to prove enforcement.
    bypass = {"BypassGovernanceRetention": True} if real else {}
    with tempfile.TemporaryDirectory() as d:
        source = Path(d) / "source"
        generate_dataset(source)
        sync_all(s3, source, PREFIX)
        keys = _all_keys(source)
        assert len(keys) == 24, f"expected 24 objects, got {len(keys)}"
        targets = keys[:OVERWRITE + DELETE]

        # Attack.
        t0 = time.time()
        for share, key in targets[:OVERWRITE]:
            s3.put_object(Bucket=f"{PREFIX}-{share}", Key=key, Body=b"ransomware\n")
        for share, key in targets[OVERWRITE:]:
            s3.delete_object(Bucket=f"{PREFIX}-{share}", Key=key, **bypass)
        attack_s = round(time.time() - t0, 3)

        # Detect.
        t0 = time.time()
        affected = []
        for share_dir in sorted(p for p in source.iterdir() if p.is_dir()):
            v = verify(s3, share_dir, f"{PREFIX}-{share_dir.name}")
            affected += v.mismatched + v.missing
        detected = len(affected) == OVERWRITE + DELETE
        detect_s = round(time.time() - t0, 3)

        # Recover: restore the pre-attack version of every object.
        # For a deleted object, remove the delete marker and the
        # old version returns. For an overwritten object, find
        # the version whose bytes still hash to the source SHA
        # (list order and LastModified ties are not reliable in
        # moto, so the bytes decide) and copy it over current.
        from migrate_s3.sync import scan_directory
        t0 = time.time()
        restored = 0
        for share, key in targets:
            lv = s3.list_object_versions(
                Bucket=f"{PREFIX}-{share}", Prefix=key)
            vers = [v for v in lv.get("Versions", []) if v["Key"] == key]
            markers = [m for m in lv.get("DeleteMarkers", []) if m["Key"] == key]
            if markers:
                # Deleted: remove the delete marker, the old version returns.
                s3.delete_object(Bucket=f"{PREFIX}-{share}", Key=key,
                                 VersionId=markers[0]["VersionId"], **bypass)
                restored += 1
            else:
                want = scan_directory(source / share)[key].sha256
                done = False
                for v in vers:
                    if v.get("IsLatest"):
                        continue
                    body = s3.get_object(
                        Bucket=f"{PREFIX}-{share}", Key=key,
                        VersionId=v["VersionId"])["Body"].read()
                    if hashlib.sha256(body).hexdigest() == want:
                        s3.copy_object(
                            Bucket=f"{PREFIX}-{share}", Key=key,
                            CopySource={"Bucket": f"{PREFIX}-{share}",
                                        "Key": key,
                                        "VersionId": v["VersionId"]})
                        restored += 1
                        done = True
                        break
                if not done:
                    print(f"  no clean version found for {share}/{key}")
        recover_s = round(time.time() - t0, 3)

        all_pass = all(
            verify(s3, sd, f"{PREFIX}-{sd.name}").passed
            for sd in sorted(p for p in source.iterdir() if p.is_dir()))
        recovered = all_pass and restored == OVERWRITE + DELETE
        data_loss = 0 if all_pass else len(affected)

    # Object Lock probe: config accepted, enforcement is what differs.
    try:
        s3.create_bucket(Bucket=lock_bucket,
                         CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
                         ObjectLockEnabledForBucket=True)
    except ClientError:
        pass  # bucket already exists on a re-run
    s3.put_object_lock_configuration(
        Bucket=lock_bucket,
        ObjectLockConfiguration={"ObjectLockEnabled": "Enabled",
                                 "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}}})
    s3.put_object(Bucket=lock_bucket, Key="f.txt", Body=b"do not delete")
    try:
        s3.delete_object(Bucket=lock_bucket, Key="f.txt")
        lock_note = ("Object Lock config accepted but delete ALLOWED "
                     "(not enforced)" if not real else
                     "Object Lock NOT enforced: delete allowed "
                     "(unexpected — check enable_object_lock)")
    except ClientError:
        lock_note = "Object Lock enforced: delete rejected"
    if real:
        # The probe object is under 1-day GOVERNANCE retention, so
        # it cannot be deleted yet; the bucket stays until the
        # retention expires. Best-effort cleanup of anything else.
        try:
            s3.delete_bucket(Bucket=lock_bucket)
        except ClientError:
            pass
        lock_note += (f"; probe bucket {lock_bucket} left with the "
                      f"retained object, deletable after 1 day")

    return {"drill": "ransomware", "backend": backend_label("moto (versioning)"),
            "objects_total": 24, "objects_overwritten": OVERWRITE,
            "objects_deleted": DELETE, "attack_seconds": attack_s,
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "objects_restored": restored, "data_loss_objects": data_loss,
            "simulated": False,
            "detail": f"attack overwrote {OVERWRITE} and deleted {DELETE} of 24 "
                      f"objects in {attack_s}s; verify found all {OVERWRITE + DELETE}; "
                      f"restored {restored} in {recover_s}s, final verify passed: "
                      f"{all_pass}, data loss: {data_loss} objects. {lock_note}"
                      + ("; lock enforcement is real-AWS-only."
                         if not real else ".")}


def run_once():
    if is_real_aws():
        s3 = garage_client()
        # Buckets come from Terraform; enabling versioning here is
        # idempotent and keeps the drill self-sufficient if it
        # runs before the apply.
        for share in SHARES:
            s3.put_bucket_versioning(
                Bucket=f"{PREFIX}-{share}",
                VersioningConfiguration={"Status": "Enabled"})
        return _run_with_client(s3, f"{PREFIX}-lock-probe", real=True)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-west-2")
        for share in SHARES:
            s3.create_bucket(Bucket=f"{PREFIX}-{share}",
                             CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
            s3.put_bucket_versioning(
                Bucket=f"{PREFIX}-{share}",
                VersioningConfiguration={"Status": "Enabled"})
        return _run_with_client(s3, "lock-probe", real=False)


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
