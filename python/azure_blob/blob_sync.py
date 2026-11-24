"""Azure Blob sync engine: scan source, compare by SHA-256, upload only what changed.

Blob mirror of migrate_s3/sync.py: same rules, Azure Blob API instead
of S3. One container per share, same 4 shares, same dataset.

Rules, in order:
1. A file is uploaded only if it is new in the container, or its
   SHA-256 differs from the blob actually stored in Azure. The
   manifest is a resume hint, not proof - the blob bytes are
   re-hashed on a re-sync, so a corrupted blob is repaired instead
   of skipped.
2. The SHA-256 is stored in blob metadata (sha256). Blob ETags are
   not SHA-256, so they are never used as a checksum. (S3 also
   stored it as a tag; Blob tags exist but metadata is the single
   store here, so there is one place to check.)
3. Quota is checked before any upload in a share. An over-quota
   share is skipped entirely and reported, never half-copied.
   Azure has no native per-container quota, so the engine enforces
   it from quotas.json before upload, exactly like the S3 engine.
4. Dry-run uploads nothing and writes no manifest.
5. --delete removes blobs that no longer exist in the source, but
   only when the flag is given. The default is to leave orphans.
6. The manifest is one blob per container, name _manifest.json.
7. Staged-block upload: files >= 8 MiB upload via stage_block /
   commit_block_list (8 MiB blocks), so a large file is one
   resumable upload instead of one huge PUT. Files below the
   threshold use upload_blob. Uncommitted blocks are abandoned on
   failure, never left as a partial blob: the blob is not visible
   until commit_block_list succeeds.
8. ACL: Azure Blob has no SMB ACLs, so the NAS group mapping is
   stored in blob metadata (owner, nas-group) plus acl.json as the
   audit trail, mirroring the S3 engine's tag approach. On real
   Azure the group would be an Entra ID group with a container
   RBAC assignment; that is real-Azure-only and is not claimed here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from azure.storage.blob import BlobServiceClient

MANIFEST_BLOB = "_manifest.json"
CHUNK = 1024 * 1024
STAGED_THRESHOLD = 8 * 1024 * 1024
STAGED_BLOCK_SIZE = 8 * 1024 * 1024

DEVSTORE_ACCOUNT = "devstoreaccount1"
DEVSTORE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
    "K1SZFPTOtr/KBHBeksoGMGw=="
)
DEFAULT_BLOB_ENDPOINT = "http://127.0.0.1:10000/devstoreaccount1"


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


def make_blob_service_client(
    blob_endpoint: str = DEFAULT_BLOB_ENDPOINT,
    account_name: str = DEVSTORE_ACCOUNT,
    account_key: str = DEVSTORE_KEY,
) -> BlobServiceClient:
    """One factory for every Blob call. The defaults point at the
    local Azurite emulator; pass a real storage account endpoint
    and key for real Azure."""
    conn = (
        "DefaultEndpointsProtocol=http;"
        f"AccountName={account_name};AccountKey={account_key};"
        f"BlobEndpoint={blob_endpoint};"
    )
    return BlobServiceClient.from_connection_string(conn)


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


def list_container_blobs(service: BlobServiceClient, container: str) -> dict[str, dict]:
    """Return {name: {size, sha256}} for every blob except the manifest."""
    blobs: dict[str, dict] = {}
    cc = service.get_container_client(container)
    for b in cc.list_blobs(include=["metadata"]):
        if b.name == MANIFEST_BLOB:
            continue
        meta = b.metadata or {}
        blobs[b.name] = {"size": b.size, "sha256": meta.get("sha256")}
    return blobs


def load_manifest(service: BlobServiceClient, container: str) -> dict[str, dict]:
    try:
        bc = service.get_blob_client(container, MANIFEST_BLOB)
        return json.loads(bc.download_blob().readall().decode("utf-8"))["files"]
    except Exception:
        return {}


def save_manifest(service: BlobServiceClient, container: str,
                  records: dict[str, FileRecord]) -> None:
    payload = {
        "version": 1,
        "files": {
            rel: {"size": r.size, "sha256": r.sha256, "mtime": r.mtime}
            for rel, r in records.items()
        },
    }
    bc = service.get_blob_client(container, MANIFEST_BLOB)
    bc.upload_blob(
        json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"),
        overwrite=True, content_type="application/json",
    )


def _blob_sha256(service: BlobServiceClient, container: str, name: str) -> str | None:
    try:
        bc = service.get_blob_client(container, name)
        return hashlib.sha256(bc.download_blob().readall()).hexdigest()
    except Exception:
        return None


def _staged_upload(service: BlobServiceClient, container: str, name: str,
                   path: Path, metadata: dict) -> None:
    """Upload a large file in 8 MiB staged blocks. Abandons the
    blocks (never commits) on failure, so no partial blob appears."""
    bc = service.get_blob_client(container, name)
    block_ids: list[str] = []
    try:
        with path.open("rb") as f:
            part_no = 0
            while True:
                data = f.read(STAGED_BLOCK_SIZE)
                if not data:
                    break
                block_id = base64.b64encode(f"{part_no:06d}".encode()).decode()
                bc.stage_block(block_id, data)
                block_ids.append(block_id)
                part_no += 1
        bc.commit_block_list(block_ids, metadata=metadata)
    except Exception:
        # No commit: the staged blocks stay uncommitted (visible via
        # get_block_list("uncommitted")) and the blob is not created.
        # Azurite/Azure garbage-collect uncommitted blocks after 7 days.
        raise


def _upload_blob(service: BlobServiceClient, container: str, name: str,
                 path: Path, metadata: dict) -> None:
    """One upload, staged blocks for large files, upload_blob otherwise."""
    if path.stat().st_size >= STAGED_THRESHOLD:
        _staged_upload(service, container, name, path, metadata)
    else:
        bc = service.get_blob_client(container, name)
        bc.upload_blob(path.read_bytes(), overwrite=True, metadata=metadata)


def sync_one_share(
    service: BlobServiceClient,
    source: Path,
    container: str,
    *,
    dry_run: bool = False,
    delete: bool = False,
    quota_bytes: int | None = None,
    acl_entry: dict | None = None,
) -> SyncResult:
    result = SyncResult(started_at=time.time())
    source_records = scan_directory(source)
    manifest = load_manifest(service, container)
    target_before = list_container_blobs(service, container)

    total_bytes = sum(r.size for r in source_records.values())
    if quota_bytes is not None and total_bytes > quota_bytes:
        result.skipped_quota = sorted(source_records)
        result.finished_at = time.time()
        return result

    new_manifest: dict[str, FileRecord] = {}
    for rel in sorted(source_records):
        rec = source_records[rel]
        prior = manifest.get(rel)
        # Unchanged means manifest matches AND the bytes in Azure
        # still hash to the same value. A corrupted blob falls
        # through to re-upload.
        if (
            prior
            and prior["size"] == rec.size
            and prior["sha256"] == rec.sha256
            and rel in target_before
            and _blob_sha256(service, container, rel) == rec.sha256
        ):
            result.skipped_unchanged.append(rel)
        else:
            if not dry_run:
                metadata = {"sha256": rec.sha256}
                if acl_entry:
                    metadata["owner"] = acl_entry.get("owner", "")
                    metadata["nas-group"] = acl_entry.get("group", "")
                _upload_blob(service, container, rel, source / rel, metadata)
                # Read back the upload and verify it before claiming success.
                if _blob_sha256(service, container, rel) != rec.sha256:
                    raise IOError(f"upload verification failed for {rel}")
            result.copied.append(rel)
            result.bytes_copied += rec.size
        new_manifest[rel] = rec

    if delete:
        cc = service.get_container_client(container)
        for rel in sorted(target_before):
            if rel not in source_records:
                if not dry_run:
                    cc.get_blob_client(rel).delete_blob()
                result.deleted.append(rel)

    if not dry_run:
        save_manifest(service, container, new_manifest)

    result.finished_at = time.time()
    return result


def ensure_container(service: BlobServiceClient, container: str) -> None:
    try:
        service.create_container(container)
    except Exception:
        pass


def sync_all(
    service: BlobServiceClient,
    source_root: Path,
    container_prefix: str,
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
        container = f"{container_prefix}-{share_dir.name}"
        ensure_container(service, container)
        results[share_dir.name] = sync_one_share(
            service, share_dir, container,
            dry_run=dry_run, delete=delete,
            quota_bytes=quotas.get(share_dir.name),
            acl_entry=acl_map.get(share_dir.name),
        )
    return results
