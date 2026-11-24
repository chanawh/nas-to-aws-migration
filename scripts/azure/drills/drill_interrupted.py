"""Drill (d): interrupted upload (abandoned staged blocks).

Inject: stage 2 blocks for a blob and never commit them, as a
client killed mid-upload would.
Detect: get_block_list("uncommitted") shows the 2 staged blocks,
while the blob itself is not visible in the container listing.
Recover: stage the blocks again and commit; the blob downloads
with the source SHA-256.
Backend: Azurite (uncommitted blocks are observable, verified live).
Real Azure garbage-collects uncommitted blocks after 7 days;
Azurite GC timing differs - no assertion is made on GC.
"""
import base64
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (BACKEND, CONTAINER_PREFIX, azurite_client, empty_all)
from migrate_s3.dataset import generate_dataset
from azure_blob.blob_sync import sync_all

BLOCK_SIZE = 8 * 1024 * 1024


def run_once():
    service = azurite_client()
    empty_all(service)
    with tempfile.TemporaryDirectory() as d:
        source = Path(d) / "source"
        generate_dataset(source)
        sync_all(service, source, CONTAINER_PREFIX)

        container = f"{CONTAINER_PREFIX}-finance"
        blob_name = "interrupted-9mb.bin"
        data = b"B" * (9 * 1024 * 1024)
        bc = service.get_blob_client(container, blob_name)
        block_ids = []
        for i in range(2):
            block_id = base64.b64encode(f"{i:06d}".encode()).decode()
            bc.stage_block(block_id, data[i * BLOCK_SIZE:(i + 1) * BLOCK_SIZE])
            block_ids.append(block_id)
        # Client dies here: no commit_block_list call.

        t0 = time.time()
        # Default block_list_type is "committed"; the residue of an
        # interrupted upload is in the "uncommitted" list.
        _, uncommitted = bc.get_block_list(block_list_type="uncommitted")
        visible = blob_name in [
            b.name for b in service.get_container_client(container).list_blobs()]
        detected = len(uncommitted) == 2 and not visible
        detect_s = round(time.time() - t0, 3)

        t0 = time.time()
        # Recover: fresh staged upload of the same bytes, then commit.
        block_ids = []
        for i in range(2):
            block_id = base64.b64encode(f"{i:06d}".encode()).decode()
            bc.stage_block(block_id, data[i * BLOCK_SIZE:(i + 1) * BLOCK_SIZE])
            block_ids.append(block_id)
        bc.commit_block_list(block_ids)
        body = bc.download_blob().readall()
        recovered = hashlib.sha256(body).hexdigest() == hashlib.sha256(data).hexdigest()
        recover_s = round(time.time() - t0, 3)

    return {"drill": "interrupted_upload", "backend": BACKEND,
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": False,
            "detail": f"uncommitted blocks listed: {len(uncommitted)}, "
                      f"blob visible before commit: {visible}; resumed "
                      f"upload SHA-256 matches: {recovered}"}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
