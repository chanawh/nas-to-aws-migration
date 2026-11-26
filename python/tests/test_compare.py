"""Tests for the migration comparison harness
(scripts/compare_migration.py).

The engine path runs live against moto; the s3 sync path is tested
with a fake command runner (the real AWS CLI is exercised by the
Garage run, not by the unit tests); the DataSync path is tested
with a fake client, since DataSync is a managed service and is
not emulated.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import boto3
from moto import mock_aws

from migrate_s3.dataset import generate_dataset
from migrate_s3.sync import make_s3_client, sync_all

import compare_migration

PREFIX = "nas2aws-migration"
SHARES = ["finance", "it-support", "it-operations", "operations"]


class FakeRun:
    """A fake `aws s3 sync`: uploads the source files to the bucket
    named in the argv, so the harness's verify pass can check them."""

    def __init__(self, s3):
        self.s3 = s3
        self.calls = []

    def __call__(self, argv, capture_output=False, text=False):
        self.calls.append(argv)
        source = Path(argv[3])
        bucket = argv[4].removeprefix("s3://")
        for f in sorted(source.rglob("*")):
            if f.is_file():
                self.s3.put_object(
                    Bucket=bucket, Key=f.relative_to(source).as_posix(),
                    Body=f.read_bytes())
        return mock.Mock(returncode=0, stdout="", stderr="")


class FakeDataSync:
    """A fake DataSync client: QUEUED, then SUCCESS with a result."""

    def __init__(self, statuses=("QUEUED", "SUCCESS")):
        self.statuses = list(statuses)
        self.calls = 0

    def start_task_execution(self, TaskArn):
        return {"TaskExecutionArn": "arn:aws:datasync:us-west-2:123:task/exec"}

    def describe_task_execution(self, TaskExecutionArn):
        self.calls += 1
        status = self.statuses[min(self.calls - 1, len(self.statuses) - 1)]
        return {"Status": status,
                "FilesTransferred": 6 if status == "SUCCESS" else 0,
                "BytesTransferred": 12345 if status == "SUCCESS" else 0,
                "BytesWritten": 12345 if status == "SUCCESS" else 0,
                "Result": {"VerifyStatus": "SUCCESS",
                           "TransferStatus": "SUCCESS",
                           "PrepareStatus": "SUCCESS",
                           "ErrorCode": "", "ErrorDetail": ""}}


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()
        self.s3 = make_s3_client(None, region="us-west-2")

    def tearDown(self):
        self.mock.stop()

    def test_method_bucket_names(self):
        self.assertEqual(
            compare_migration.method_bucket(PREFIX, "engine", "finance"),
            "nas2aws-migration-engine-finance")
        self.assertEqual(
            compare_migration.method_bucket(PREFIX, "s3sync", "operations"),
            "nas2aws-migration-s3sync-operations")

    def test_find_aws_cli(self):
        # The venv has awscli installed (pip install awscli), next to
        # the Python running these tests.
        found = compare_migration.find_aws_cli()
        self.assertIsNotNone(found)
        self.assertTrue(Path(found).name == "aws")

    def test_build_s3sync_argv(self):
        argv = compare_migration.build_s3sync_argv(
            "aws", Path("/src/finance"), "my-bucket",
            "http://127.0.0.1:3900", "us-west-2")
        self.assertEqual(argv, ["aws", "s3", "sync", "/src/finance",
                                "s3://my-bucket", "--region", "us-west-2",
                                "--endpoint-url", "http://127.0.0.1:3900"])
        # Real AWS: no --endpoint-url. The binary path is argv[0].
        argv = compare_migration.build_s3sync_argv(
            "/venv/bin/aws", Path("/src/finance"), "my-bucket",
            None, "us-west-2")
        self.assertEqual(argv[0], "/venv/bin/aws")
        self.assertNotIn("--endpoint-url", argv)

    def test_create_bucket(self):
        compare_migration.create_bucket(self.s3, "my-bucket", "us-west-2")
        # Idempotent: creating again is not an error.
        compare_migration.create_bucket(self.s3, "my-bucket", "us-west-2")
        self.assertEqual(
            self.s3.head_bucket(Bucket="my-bucket")
            ["ResponseMetadata"]["HTTPStatusCode"], 200)

    def test_engine_method_runs_and_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            rec = compare_migration.run_engine_method(
                self.s3, source, PREFIX, "us-west-2", 1,
                client_factory=lambda: self.s3)
            self.assertTrue(rec["ran"])
            self.assertEqual(rec["objects"], 24)
            self.assertTrue(rec["verify"]["passed"])
            self.assertEqual(rec["verify"]["checked"], 24)
            self.assertGreater(rec["seconds"], 0)
            self.assertGreater(rec["mb_s"], 0)

    def test_s3sync_method_with_fake_cli(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            fake = FakeRun(self.s3)
            rec = compare_migration.run_s3sync_method(
                source, PREFIX, "http://127.0.0.1:3900", "us-west-2",
                self.s3, aws_bin="aws", run_cmd=fake)
            self.assertTrue(rec["ran"])
            self.assertEqual(rec["objects"], 24)
            self.assertTrue(rec["verify"]["passed"])
            self.assertEqual(len(fake.calls), 4)  # one sync per share

    def test_s3sync_method_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            failing = mock.Mock(
                return_value=mock.Mock(returncode=1, stdout="",
                                       stderr="An error occurred"))
            rec = compare_migration.run_s3sync_method(
                source, PREFIX, None, "us-west-2",
                self.s3, aws_bin="aws", run_cmd=failing)
            self.assertFalse(rec["ran"])
            self.assertIn("failed", rec["skipped_reason"])

    def test_s3sync_method_no_aws_cli_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            # No aws binary anywhere: the lookup itself is patched out.
            with mock.patch.object(compare_migration, "find_aws_cli",
                                   return_value=None):
                rec = compare_migration.run_s3sync_method(
                    source, PREFIX, None, "us-west-2",
                    self.s3, aws_bin=None, run_cmd=mock.Mock())
            self.assertFalse(rec["ran"])
            self.assertIn("aws CLI", rec["skipped_reason"])

    def test_datasync_skipped_on_emulator(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            rec = compare_migration.run_datasync_method(
                "arn:aws:datasync:us-west-2:123:task/task-1",
                self.s3, source, PREFIX, "us-west-2", real_aws=False)
            self.assertFalse(rec["ran"])
            self.assertIn("not emulated", rec["skipped_reason"])

    def test_datasync_skipped_without_task_arn(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            rec = compare_migration.run_datasync_method(
                None, self.s3, source, PREFIX, "us-west-2", real_aws=True)
            self.assertFalse(rec["ran"])
            self.assertIn("DATASYNC_TASK_ARN", rec["skipped_reason"])

    def test_datasync_method_with_fake_client(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            # The target bucket is the normal finance bucket; the
            # engine populates all four share buckets first, so the
            # verify pass can run.
            for share in SHARES:
                self.s3.create_bucket(
                    Bucket=f"{PREFIX}-{share}",
                    CreateBucketConfiguration={
                        "LocationConstraint": "us-west-2"})
            sync_all(self.s3, source, PREFIX)
            fake = FakeDataSync()
            rec = compare_migration.run_datasync_method(
                "arn:aws:datasync:us-west-2:123:task/task-1",
                self.s3, source, PREFIX, "us-west-2",
                client=fake, real_aws=True)
            self.assertTrue(rec["ran"])
            self.assertEqual(rec["shares"], ["finance"])
            self.assertEqual(rec["datasync_execution"]["files_transferred"], 6)
            self.assertEqual(
                rec["datasync_execution"]["verify_status"], "SUCCESS")
            self.assertTrue(rec["verify"]["passed"])

    def test_wait_task_execution_times_out(self):
        fake = FakeDataSync(statuses=("QUEUED",))
        with self.assertRaises(TimeoutError):
            compare_migration.wait_task_execution(
                fake, "arn:exec", timeout_s=0.01, interval_s=0.01,
                sleep_fn=lambda s: None)


if __name__ == "__main__":
    unittest.main()
