"""Drill 5: interrupted multipart upload.

Inject: start a multipart upload, upload two parts, then walk
away without completing it, as a crashed client would.
Detect: list_multipart_uploads shows the orphaned upload, and
the object does not exist yet.
Recover: abort the orphan (cleanup), then start a fresh
multipart upload, upload all parts, complete it, and verify
the object's SHA-256. No partial object is ever visible.
Backend: Garage (real multipart API).
"""
import hashlib
import json
import time
from pathlib import Path

from botocore.exceptions import ClientError

from common import BUCKET_PREFIX, empty_bucket, ensure_buckets, garage_client

PART = 8 * 1024 * 1024
KEY = "interrupted-9mb.bin"


def run_once():
    s3 = garage_client()
    ensure_buckets(s3)
    bucket = f"{BUCKET_PREFIX}-finance"
    empty_bucket(s3, bucket)
    data = (hashlib.sha256(b"multipart-drill").digest() * (9 * 1024 * 1024 // 32 + 1))[:9 * 1024 * 1024]
    want = hashlib.sha256(data).hexdigest()

    # Interrupt: two parts uploaded, never completed.
    up = s3.create_multipart_upload(Bucket=bucket, Key=KEY)
    uid = up["UploadId"]
    for n in range(1, 3):
        s3.upload_part(Bucket=bucket, Key=KEY, PartNumber=n, UploadId=uid,
                       Body=data[(n - 1) * PART:n * PART])

    t0 = time.time()
    orphans = s3.list_multipart_uploads(Bucket=bucket).get("Uploads", [])
    orphan_found = any(u["UploadId"] == uid for u in orphans)
    try:
        s3.head_object(Bucket=bucket, Key=KEY)
        object_visible = True
    except ClientError:
        object_visible = False
    detected = orphan_found and not object_visible
    detect_s = round(time.time() - t0, 3)

    # Recover: abort the orphan, then a fresh multipart upload.
    t0 = time.time()
    s3.abort_multipart_upload(Bucket=bucket, Key=KEY, UploadId=uid)
    left = s3.list_multipart_uploads(Bucket=bucket).get("Uploads", [])
    up2 = s3.create_multipart_upload(Bucket=bucket, Key=KEY,
                                     Metadata={"sha256": want})
    uid2 = up2["UploadId"]
    parts = []
    for n in range(1, 3):
        p = s3.upload_part(Bucket=bucket, Key=KEY, PartNumber=n, UploadId=uid2,
                           Body=data[(n - 1) * PART:n * PART])
        parts.append({"PartNumber": n, "ETag": p["ETag"]})
    s3.complete_multipart_upload(Bucket=bucket, Key=KEY, UploadId=uid2,
                                 MultipartUpload={"Parts": parts})
    got = hashlib.sha256(s3.get_object(Bucket=bucket, Key=KEY)["Body"].read()).hexdigest()
    recovered = got == want and not left
    recover_s = round(time.time() - t0, 3)

    return {"drill": "interrupted_multipart", "backend": "Garage v1.3.0",
            "detected": detected, "detect_seconds": detect_s,
            "recovered": recovered, "recover_seconds": recover_s,
            "simulated": False,
            "detail": f"orphan upload listed: {orphan_found}, object visible "
                      f"before completion: {object_visible}; resumed upload "
                      f"SHA-256 matches: {got == want}"}


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
