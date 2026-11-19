"""S3 sync engine: scan source, compare by SHA-256, upload only what changed.

Rules, in order:
1. A file is uploaded only if it is new in the bucket, or its SHA-256
   differs from the object actually stored in S3. The manifest is a
   resume hint, not proof - the object bytes are re-hashed on a
   re-sync, so a corrupted object is repaired instead of skipped.
   (This is the bug the Azure twin caught in its first live run.)
2. The SHA-256 is stored in object Metadata (sha256) and as a Tag.
   S3 ETags are not SHA-256, so they are never used as a checksum.
3. Quota is checked before any upload in a share. An over-quota share
   is skipped entirely and reported, never half-copied.
4. Dry-run uploads nothing and writes no manifest.
5. --delete removes objects that no longer exist in the source, but
   only when the flag is given. The default is to leave orphans.
6. The manifest is one object per bucket, key _manifest.json.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

MANIFEST_KEY = "_manifest.json"
CHUNK = 1024 * 1024


@dataclass
class FileRecord:
    rel: str
    size: int
    sha256: str
    mtime: float


@dataclass
class SyncResult:
    copied: list[str] = field(default_factory=list)
    skipped_unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped_quota: list[str] = field(default_factory=list)
    bytes_copied: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration_s(self) -> float:
        return round(self.finished_at - self.started_at, 3)


def make_s3_client(endpoint_url: str | None, region: str = "us-west-2"):
    """One factory for every S3 call. endpoint_url=None means real AWS."""
    kwargs: dict = {
        "service_name": "s3",
        "region_name": region,
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
    }
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client(**kwargs)


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def scan_directory(root: Path) -> dict[str, FileRecord]:
    records: dict[str, FileRecord] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        records[rel] = FileRecord(
            rel=rel, size=p.stat().st_size,
            sha256=compute_sha256(p), mtime=p.stat().st_mtime,
        )
    return records


def list_bucket_objects(s3, bucket: str) -> dict[str, dict]:
    """Return {key: {size, etag}} for every object except the manifest."""
    objects: dict[str, dict] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            if obj["Key"] == MANIFEST_KEY:
                continue
            objects[obj["Key"]] = {"size": obj["Size"], "etag": obj["ETag"]}
    return objects


def load_manifest(s3, bucket: str) -> dict[str, dict]:
    try:
        resp = s3.get_object(Bucket=bucket, Key=MANIFEST_KEY)
        return json.loads(resp["Body"].read().decode("utf-8"))["files"]
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {}
        raise


def save_manifest(s3, bucket: str, records: dict[str, FileRecord]) -> None:
    payload = {
        "version": 1,
        "files": {
            rel: {"size": r.size, "sha256": r.sha256, "mtime": r.mtime}
            for rel, r in records.items()
        },
    }
    s3.put_object(
        Bucket=bucket, Key=MANIFEST_KEY,
        Body=json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )


def _object_sha256(s3, bucket: str, key: str) -> str | None:
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        return hashlib.sha256(resp["Body"].read()).hexdigest()
    except ClientError:
        return None


def sync_one_share(
    s3,
    source: Path,
    bucket: str,
    *,
    dry_run: bool = False,
    delete: bool = False,
    quota_bytes: int | None = None,
    acl_entry: dict | None = None,
) -> SyncResult:
    result = SyncResult(started_at=time.time())
    source_records = scan_directory(source)
    manifest = load_manifest(s3, bucket)
    target_before = list_bucket_objects(s3, bucket)

    total_bytes = sum(r.size for r in source_records.values())
    if quota_bytes is not None and total_bytes > quota_bytes:
        result.skipped_quota = sorted(source_records)
        result.finished_at = time.time()
        return result

    new_manifest: dict[str, FileRecord] = {}
    for rel, rec in source_records.items():
        prior = manifest.get(rel)
        # Unchanged means manifest matches AND the bytes in S3 still hash
        # to the same value. A corrupted object falls through to re-upload.
        if (
            prior
            and prior["size"] == rec.size
            and prior["sha256"] == rec.sha256
            and rel in target_before
            and _object_sha256(s3, bucket, rel) == rec.sha256
        ):
            result.skipped_unchanged.append(rel)
            new_manifest[rel] = rec
            continue
        if not dry_run:
            metadata = {"sha256": rec.sha256}
            tagging = f"sha256={rec.sha256}"
            if acl_entry:
                metadata["owner"] = acl_entry.get("owner", "")
                metadata["nas-group"] = acl_entry.get("group", "")
                tagging += f"&owner={acl_entry.get('owner', '')}&nas-group={acl_entry.get('group', '')}"
            s3.put_object(
                Bucket=bucket, Key=rel,
                Body=(source / rel).read_bytes(),
                Metadata=metadata, Tagging=tagging,
            )
            # Read back the upload and verify it before claiming success.
            if _object_sha256(s3, bucket, rel) != rec.sha256:
                raise IOError(f"upload verification failed for {rel}")
        result.copied.append(rel)
        result.bytes_copied += rec.size
        new_manifest[rel] = rec

    if delete:
        for rel in sorted(target_before):
            if rel not in source_records:
                if not dry_run:
                    s3.delete_object(Bucket=bucket, Key=rel)
                result.deleted.append(rel)

    if not dry_run:
        save_manifest(s3, bucket, new_manifest)

    result.finished_at = time.time()
    return result


def sync_all(
    s3,
    source_root: Path,
    bucket_prefix: str,
    *,
    dry_run: bool = False,
    delete: bool = False,
    quotas: dict[str, int] | None = None,
    acl_map: dict[str, dict] | None = None,
) -> dict[str, SyncResult]:
    quotas = quotas or {}
    acl_map = acl_map or {}
    results: dict[str, SyncResult] = {}
    for share_dir in sorted(p for p in source_root.iterdir() if p.is_dir()):
        results[share_dir.name] = sync_one_share(
            s3, share_dir, f"{bucket_prefix}-{share_dir.name}",
            dry_run=dry_run, delete=delete,
            quota_bytes=quotas.get(share_dir.name),
            acl_entry=acl_map.get(share_dir.name),
        )
    return results
