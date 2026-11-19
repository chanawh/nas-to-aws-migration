"""S3 object validator Lambda.

Triggered by SQS, which is fed by S3 ObjectCreated notifications.
For each SQS message it parses the S3 event, re-downloads the
object, re-hashes it, compares it to the SHA-256 in object Metadata,
and writes the verdict to the DynamoDB audit table. A mismatch is
also published to the SNS alerts topic.

Env vars (set by Terraform):
  AUDIT_TABLE, ALERT_TOPIC_ARN, ENDPOINT_URL (empty on real AWS,
  the emulator URL on LocalStack / moto).
Stdlib + boto3 only, same as the sync engine.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import boto3

AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "nas2aws-migration-audit")
ALERT_TOPIC_ARN = os.environ.get("ALERT_TOPIC_ARN", "")
ENDPOINT_URL = os.environ.get("ENDPOINT_URL") or None


def _client(service):
    kwargs = {"service_name": service, "region_name": os.environ.get("AWS_REGION", "us-west-2")}
    if ENDPOINT_URL:
        kwargs["endpoint_url"] = ENDPOINT_URL
    return boto3.client(**kwargs)


def _validate_one(s3, bucket: str, key: str) -> dict:
    head = s3.head_object(Bucket=bucket, Key=key)
    expected = (head.get("Metadata") or {}).get("sha256", "")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    actual = hashlib.sha256(body).hexdigest()
    return {
        "bucket": bucket,
        "key": key,
        "expected_sha256": expected,
        "actual_sha256": actual,
        "size": head.get("ContentLength", len(body)),
        "status": "ok" if expected and expected == actual else "mismatch",
    }


def _write_audit(ddb, share: str, result: dict) -> None:
    ddb.put_item(
        TableName=AUDIT_TABLE,
        Item={
            "share": {"S": share},
            "object_key": {"S": result["key"]},
            "status": {"S": f"validated-{result['status']}"},
            "sha256": {"S": result["actual_sha256"]},
            "size": {"N": str(result["size"])},
            "validated_at": {"N": str(int(time.time()))},
            "expires_at": {"N": str(int(time.time()) + 90 * 24 * 3600)},
        },
    )


def handler(event, context=None):
    s3 = _client("s3")
    ddb = _client("dynamodb")
    sns = _client("sns") if ALERT_TOPIC_ARN else None
    results = []
    for record in event.get("Records", []):
        body = record.get("body", "")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        for s3_record in payload.get("Records", []):
            s3info = s3_record.get("s3", {})
            bucket = s3info.get("bucket", {}).get("name", "")
            key = s3info.get("object", {}).get("key", "")
            if not bucket or not key or key == "_manifest.json":
                continue
            share = bucket.rsplit("-", 1)[-1] if "-" in bucket else bucket
            result = _validate_one(s3, bucket, key)
            _write_audit(ddb, share, result)
            results.append(result)
            if result["status"] != "ok" and sns:
                sns.publish(
                    TopicArn=ALERT_TOPIC_ARN,
                    Subject=f"NAS-to-AWS validator mismatch: {bucket}/{key}",
                    Message=json.dumps(result, indent=2),
                )
    return {"validated": len(results), "mismatches": sum(1 for r in results if r["status"] != "ok")}
