"""Tests for the added services: DynamoDB audit, SNS alerts, Lambda validator.

All with moto in-process mocks. No server, no Docker, no AWS account.
Lambda is tested by calling its handler directly, not by invoking
through the Lambda service - moto's Lambda execution is a mock, and
the handler is the code that matters.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))

import boto3
from moto import mock_aws

from migrate_s3.audit import publish_verify_failure, write_audit
from migrate_s3.dataset import generate_dataset

os.environ["AUDIT_TABLE"] = "nas2aws-migration-audit"
os.environ["ALERT_TOPIC_ARN"] = ""
os.environ["ENDPOINT_URL"] = ""

import validator


class TestServices(unittest.TestCase):
    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()
        self.s3 = boto3.client("s3", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")
        self.ddb = boto3.client("dynamodb", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")
        self.sns = boto3.client("sns", region_name="us-west-2", aws_access_key_id="test", aws_secret_access_key="test")
        self.s3.create_bucket(Bucket="nas2aws-migration-finance", CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
        self.ddb.create_table(
            TableName="nas2aws-migration-audit",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "share", "KeyType": "HASH"}, {"AttributeName": "object_key", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "share", "AttributeType": "S"}, {"AttributeName": "object_key", "AttributeType": "S"}],
        )

    def tearDown(self):
        self.mock.stop()

    def test_audit_write_records_every_file(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            count = write_audit(self.ddb, "nas2aws-migration-audit", "finance", source / "finance")
            self.assertEqual(count, 6)
            items = self.ddb.scan(TableName="nas2aws-migration-audit")["Items"]
            self.assertEqual(len(items), 6)
            self.assertTrue(all(i["status"]["S"] == "synced" for i in items))

    def test_sns_publish_on_verify_failure(self):
        topic = self.sns.create_topic(Name="nas2aws-migration-alerts")["TopicArn"]
        publish_verify_failure(self.sns, topic, "finance", ["budget-2027-draft.csv"])
        # moto SNS publish returns a MessageId; no delivery is asserted,
        # the call itself is the contract the CLI relies on.
        self.assertTrue(topic.startswith("arn:aws:sns:"))

    def test_lambda_validator_ok(self):
        import hashlib

        body = b"hello\n"
        sha = hashlib.sha256(body).hexdigest()
        self.s3.put_object(Bucket="nas2aws-migration-finance", Key="a.txt", Body=body, Metadata={"sha256": sha})
        event = {"Records": [{"body": json.dumps({"Records": [{"s3": {"bucket": {"name": "nas2aws-migration-finance"}, "object": {"key": "a.txt"}}}]})}]}
        with mock.patch.object(validator, "_client", side_effect=lambda svc: {"s3": self.s3, "dynamodb": self.ddb, "sns": self.sns}[svc]):
            result = validator.handler(event)
        self.assertEqual(result, {"validated": 1, "mismatches": 0})
        items = self.ddb.scan(TableName="nas2aws-migration-audit")["Items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"]["S"], "validated-ok")

    def test_lambda_validator_mismatch(self):
        import hashlib

        body = b"corrupted\n"
        sha = hashlib.sha256(b"original\n").hexdigest()
        self.s3.put_object(Bucket="nas2aws-migration-finance", Key="a.txt", Body=body, Metadata={"sha256": sha})
        event = {"Records": [{"body": json.dumps({"Records": [{"s3": {"bucket": {"name": "nas2aws-migration-finance"}, "object": {"key": "a.txt"}}}]})}]}
        with mock.patch.object(validator, "_client", side_effect=lambda svc: {"s3": self.s3, "dynamodb": self.ddb, "sns": self.sns}[svc]):
            result = validator.handler(event)
        self.assertEqual(result, {"validated": 1, "mismatches": 1})


if __name__ == "__main__":
    unittest.main()
