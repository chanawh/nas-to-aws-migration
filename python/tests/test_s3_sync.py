"""Tests for the S3 sync/verify engine, using moto's in-process mock.

Run from python/ with the project venv:
    ~/workspace/nas-to-aws-migration-venv/bin/python -m unittest discover -s tests -v
No Docker, no server, no AWS account. moto emulates the S3 API in-process.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import boto3
from moto import mock_aws

from migrate_s3.acl import validate_acl
from migrate_s3.dataset import generate_dataset
from migrate_s3.quotas import check_quotas
from migrate_s3.sync import load_manifest, make_s3_client, sync_all, sync_one_share
from migrate_s3.verify import verify

PREFIX = "nas2aws-migration"
SHARES = ["finance", "it-support", "it-operations", "operations"]


def _make_source(tmp: Path) -> Path:
    source = tmp / "source"
    generate_dataset(source)
    return source


class TestS3Sync(unittest.TestCase):
    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()
        self.s3 = make_s3_client(None, region="us-west-2")
        # moto requires a location constraint for non-us-east-1 buckets.
        for share in SHARES:
            self.s3.create_bucket(
                Bucket=f"{PREFIX}-{share}",
                CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
            )

    def tearDown(self):
        self.mock.stop()

    def test_initial_sync_uploads_everything_and_verify_passes(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            results = sync_all(self.s3, source, PREFIX)
            self.assertEqual(sum(len(r.copied) for r in results.values()), 24)
            for share in SHARES:
                self.assertTrue(verify(self.s3, source / share, f"{PREFIX}-{share}").passed)

    def test_second_sync_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.s3, source, PREFIX)
            results = sync_all(self.s3, source, PREFIX)
            self.assertEqual(sum(len(r.copied) for r in results.values()), 0)
            self.assertEqual(sum(len(r.skipped_unchanged) for r in results.values()), 24)

    def test_changed_file_is_reuploaded_and_unchanged_are_not(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.s3, source, PREFIX)
            (source / "finance" / "budget-2027-draft.csv").write_text("changed\n", encoding="utf-8")
            results = sync_all(self.s3, source, PREFIX)
            self.assertEqual(results["finance"].copied, ["budget-2027-draft.csv"])
            self.assertTrue(verify(self.s3, source / "finance", f"{PREFIX}-finance").passed)

    def test_corrupted_object_is_detected_and_repaired(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.s3, source, PREFIX)
            self.s3.put_object(
                Bucket=f"{PREFIX}-finance", Key="budget-2027-draft.csv",
                Body=b"corrupted\n",
            )
            result = verify(self.s3, source / "finance", f"{PREFIX}-finance")
            self.assertFalse(result.passed)
            self.assertEqual(result.mismatched, ["budget-2027-draft.csv"])
            sync_all(self.s3, source, PREFIX)
            self.assertTrue(verify(self.s3, source / "finance", f"{PREFIX}-finance").passed)

    def test_dry_run_uploads_nothing_and_writes_no_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            results = sync_all(self.s3, source, PREFIX, dry_run=True)
            self.assertEqual(sum(len(r.copied) for r in results.values()), 24)
            self.assertEqual(load_manifest(self.s3, f"{PREFIX}-finance"), {})

    def test_delete_removes_orphan_only_with_flag(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.s3, source, PREFIX)
            (source / "finance" / "budget-2027-draft.csv").unlink()
            sync_all(self.s3, source, PREFIX)
            keys = [o["Key"] for o in self.s3.list_objects_v2(Bucket=f"{PREFIX}-finance").get("Contents", [])]
            self.assertIn("budget-2027-draft.csv", keys)
            sync_all(self.s3, source, PREFIX, delete=True)
            keys = [o["Key"] for o in self.s3.list_objects_v2(Bucket=f"{PREFIX}-finance").get("Contents", [])]
            self.assertNotIn("budget-2027-draft.csv", keys)

    def test_over_quota_share_is_skipped_whole(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            result = sync_one_share(self.s3, source / "finance", f"{PREFIX}-finance", quota_bytes=10)
            self.assertEqual(len(result.skipped_quota), 6)
            self.assertEqual(len(result.copied), 0)

    def test_acl_mapping_is_clean_for_generated_dataset(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            self.assertEqual(validate_acl(source / "users.csv", source / "acl.json"), [])

    def test_quotas_are_not_exceeded_by_generated_dataset(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            quotas = {"finance": 2 * 1024 * 1024, "it-support": 2 * 1024 * 1024,
                      "it-operations": 1 * 1024 * 1024, "operations": 1 * 1024 * 1024}
            for share, check in check_quotas(source, quotas).items():
                self.assertFalse(check["over"], share)

    def test_manifest_is_stored_as_s3_object(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.s3, source, PREFIX)
            manifest = load_manifest(self.s3, f"{PREFIX}-finance")
            self.assertEqual(len(manifest), 6)


if __name__ == "__main__":
    unittest.main()
