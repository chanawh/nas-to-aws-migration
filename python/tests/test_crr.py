"""Tests for the CRR lag measurement and failover drill
(scripts/crr_lag.py, scripts/crr_failover.py).

The polling/timing logic is tested with fake time (a clock that
only advances when the test says so); the replica verification is
tested against moto. Nothing here touches real AWS: replication
itself is real-AWS-only and is never claimed from these tests.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

from migrate_s3.sync import make_s3_client

import crr_failover
import crr_lag


class FakeClock:
    """A clock that only moves when sleep is called."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeS3:
    """A fake S3 client for the lag measurement: the replica head
    fails until `visible_after` polls, then succeeds."""

    def __init__(self, visible_after=3, replicated=True):
        self.visible_after = visible_after
        self.replicated = replicated
        self.head_calls = 0
        self.put_keys = []

    def get_bucket_replication(self, Bucket):
        if not self.replicated:
            raise ClientError(
                {"Error": {"Code": "NoSuchReplicationConfiguration"}},
                "GetBucketReplication")
        return {"ReplicationConfiguration": {"Rules": [{"ID": "to-us-east-1"}]}}

    def put_object(self, Bucket, Key, Body):
        self.put_keys.append(Key)
        return {}

    def head_object(self, Bucket, Key):
        self.head_calls += 1
        if self.head_calls < self.visible_after:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def delete_object(self, Bucket, Key):
        return {}


class TestCrrLag(unittest.TestCase):
    def test_poll_until_visible(self):
        clock = FakeClock()
        calls = []

        def head():
            calls.append(clock.now)
            return len(calls) >= 3

        visible = crr_lag.poll_until_visible(
            head, timeout_s=100, interval_s=5,
            clock=clock, sleep_fn=clock.sleep)
        self.assertEqual(visible, 1010.0)  # 2 sleeps of 5s each
        self.assertEqual(len(calls), 3)

    def test_poll_until_visible_timeout(self):
        clock = FakeClock()

        def head():
            return False

        visible = crr_lag.poll_until_visible(
            head, timeout_s=10, interval_s=5,
            clock=clock, sleep_fn=clock.sleep)
        self.assertIsNone(visible)

    def test_check_replication_config(self):
        fake = FakeS3(replicated=True)
        self.assertTrue(crr_lag.check_replication_config(fake, "b"))
        fake = FakeS3(replicated=False)
        self.assertFalse(crr_lag.check_replication_config(fake, "b"))

    def test_measure_lag(self):
        clock = FakeClock(start=5000.0)
        source = FakeS3(visible_after=3)
        replica = FakeS3(visible_after=3)
        rec = crr_lag.measure_lag(
            source, replica, "src", "dst", "_crr-canary-test.json",
            timeout_s=100, interval_s=5, clock=clock,
            sleep_fn=clock.sleep)
        self.assertEqual(rec["status"], "measured")
        # 2 failed heads (2 sleeps of 5s) before the visible head.
        self.assertEqual(rec["lag_seconds"], 10.0)
        self.assertEqual(source.put_keys, ["_crr-canary-test.json"])

    def test_measure_lag_not_configured(self):
        clock = FakeClock()
        source = FakeS3(replicated=False)
        replica = FakeS3()
        with self.assertRaises(crr_lag.ReplicationNotConfigured):
            crr_lag.measure_lag(source, replica, "src", "dst", "k",
                                clock=clock, sleep_fn=clock.sleep)

    def test_measure_lag_timeout(self):
        clock = FakeClock()
        source = FakeS3()
        replica = FakeS3(visible_after=999)
        with self.assertRaises(crr_lag.ReplicationTimeout):
            crr_lag.measure_lag(source, replica, "src", "dst", "k",
                                timeout_s=10, interval_s=5,
                                clock=clock, sleep_fn=clock.sleep)

    def test_main_refuses_emulator(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "lag.json"
            argv = ["crr_lag.py", "--bucket", "src", "--replica-bucket",
                    "dst", "--out", str(out)]
            with mock.patch.object(sys, "argv", argv), mock.patch.dict(
                    "os.environ", {"S3_ENDPOINT": "http://127.0.0.1:3900"}):
                exit_code = crr_lag.main()
            self.assertEqual(exit_code, 2)
            evidence = json.loads(out.read_text())
            self.assertEqual(evidence["status"], "not_measured")
            self.assertIn("real-AWS-only", evidence["reason"])


class TestCrrFailover(unittest.TestCase):
    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()
        self.s3 = make_s3_client(None, region="us-west-2")
        self.s3.create_bucket(
            Bucket="nas2aws-migration-finance-replica",
            CreateBucketConfiguration={"LocationConstraint": "us-west-2"})

    def tearDown(self):
        self.mock.stop()

    def _manifest(self):
        return {
            "a.txt": {"size": 5, "sha256": hashlib.sha256(b"hello").hexdigest()},
            "b.txt": {"size": 5, "sha256": hashlib.sha256(b"world").hexdigest()},
        }

    def _populate(self, corrupt=None, missing=None):
        manifest = self._manifest()
        for rel, rec in manifest.items():
            if rel == missing:
                continue
            body = b"corrupted" if rel == corrupt else (
                b"hello" if rel == "a.txt" else b"world")
            self.s3.put_object(
                Bucket="nas2aws-migration-finance-replica", Key=rel,
                Body=body)
        return manifest

    def test_verify_replica_passes(self):
        manifest = self._populate()
        result = crr_failover.verify_replica(
            self.s3, "nas2aws-migration-finance-replica", manifest)
        self.assertTrue(result["passed"])
        self.assertEqual(result["checked"], 2)

    def test_verify_replica_mismatch(self):
        manifest = self._populate(corrupt="b.txt")
        result = crr_failover.verify_replica(
            self.s3, "nas2aws-migration-finance-replica", manifest)
        self.assertFalse(result["passed"])
        self.assertEqual(result["mismatched"], ["b.txt"])

    def test_verify_replica_missing(self):
        manifest = self._populate(missing="a.txt")
        result = crr_failover.verify_replica(
            self.s3, "nas2aws-migration-finance-replica", manifest)
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing"], ["a.txt"])

    def test_failover_steps_are_recorded(self):
        self.assertEqual(len(crr_failover.FAILOVER_STEPS), 4)
        self.assertIn("replica bucket", crr_failover.FAILOVER_STEPS[1])

    def test_main_refuses_emulator(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "failover.json"
            argv = ["crr_failover.py", "--bucket", "src",
                    "--replica-bucket", "dst", "--out", str(out)]
            with mock.patch.object(sys, "argv", argv), mock.patch.dict(
                    "os.environ", {"S3_ENDPOINT": "http://127.0.0.1:3900"}):
                exit_code = crr_failover.main()
            self.assertEqual(exit_code, 2)
            evidence = json.loads(out.read_text())
            self.assertEqual(evidence["status"], "not_measured")
            self.assertIn("real-AWS-only", evidence["reason"])


if __name__ == "__main__":
    unittest.main()
