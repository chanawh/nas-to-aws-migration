"""DynamoDB audit + SNS alert wiring for the sync/verify CLI.

The S3 manifest object is the resume point. This is the evidence
trail: one DynamoDB item per object per sync, and one SNS message
per verify failure. Both are optional - pass no table / no topic
and the CLI behaves exactly as before.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import boto3

from .sync import compute_sha256


def make_dynamodb_client(endpoint_url: str | None, region: str = "us-west-2"):
    kwargs = {"service_name": "dynamodb", "region_name": region,
              "aws_access_key_id": "test", "aws_secret_access_key": "test"}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client(**kwargs)


def make_sns_client(endpoint_url: str | None, region: str = "us-west-2"):
    kwargs = {"service_name": "sns", "region_name": region,
              "aws_access_key_id": "test", "aws_secret_access_key": "test"}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client(**kwargs)


def write_audit(ddb, table: str, share: str, source_dir: Path) -> int:
    """Write one audit item per file in a share directory. Returns count."""
    count = 0
    now = int(time.time())
    for p in sorted(source_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(source_dir).as_posix()
        ddb.put_item(
            TableName=table,
            Item={
                "share": {"S": share},
                "object_key": {"S": rel},
                "status": {"S": "synced"},
                "sha256": {"S": compute_sha256(p)},
                "size": {"N": str(p.stat().st_size)},
                "validated_at": {"N": str(now)},
                "expires_at": {"N": str(now + 90 * 24 * 3600)},
            },
        )
        count += 1
    return count


def publish_verify_failure(sns, topic_arn: str, share: str, failures: list[str]) -> None:
    sns.publish(
        TopicArn=topic_arn,
        Subject=f"NAS-to-AWS verify FAIL: {share}",
        Message=json.dumps({"share": share, "failed": failures}, indent=2),
    )
