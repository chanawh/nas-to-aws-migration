"""Tests for the EFS secondary target (scripts/migrate_efs.py).

The copy/verify logic is tested against a local directory standing
in for the mount -- EFS itself is real-AWS-only and cannot be
emulated. The backend label must say so: a local stand-in is
labelled "local directory stand-in (NOT EFS)", never EFS.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from migrate_s3.dataset import generate_dataset

import migrate_efs

SHARES = ["finance", "it-support", "it-operations", "operations"]


class TestEfsCopy(unittest.TestCase):
    def test_copy_and_verify_passes(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            mount = Path(d) / "mount"
            mount.mkdir()
            evidence = migrate_efs.migrate_efs(source, mount)
            self.assertEqual(evidence["backend"],
                             "local directory stand-in (NOT EFS)")
            self.assertEqual(evidence["status"], "measured")
            self.assertTrue(evidence["verify_totals"]["passed"])
            self.assertEqual(evidence["verify_totals"]["checked"], 24)
            # One manifest per share, same shape as the S3 engine's.
            for share in SHARES:
                manifest = mount / share / "_manifest.json"
                self.assertTrue(manifest.exists())
            finance = json.loads(
                (mount / "finance" / "_manifest.json").read_text())
            self.assertIn("budget-2027-draft.csv", finance["files"])
            # 3 named files + empty-file + deep archive file + spaced name.
            self.assertEqual(len(finance["files"]), 6)

    def test_corrupted_copy_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            mount = Path(d) / "mount"
            mount.mkdir()
            migrate_efs.migrate_efs(source, mount)
            (mount / "finance" / "budget-2027-draft.csv").write_text(
                "corrupted\n", encoding="utf-8")
            v = migrate_efs.verify_mount(source / "finance", mount)
            self.assertFalse(v["passed"])
            self.assertEqual(v["mismatched"], ["budget-2027-draft.csv"])

    def test_missing_file_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            mount = Path(d) / "mount"
            mount.mkdir()
            migrate_efs.migrate_efs(source, mount)
            (mount / "operations" / "shift-plan.txt").unlink()
            v = migrate_efs.verify_mount(source / "operations", mount)
            self.assertFalse(v["passed"])
            self.assertEqual(v["missing"], ["shift-plan.txt"])

    def test_over_quota_share_is_skipped_whole(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            mount = Path(d) / "mount"
            mount.mkdir()
            quotas = {"finance": 1}  # 1 byte: the finance share cannot fit
            evidence = migrate_efs.migrate_efs(source, mount, quotas)
            self.assertTrue(evidence["shares"]["finance"]["skipped_quota"])
            self.assertFalse((mount / "finance").exists())
            # The other shares still copied and verified.
            self.assertFalse(evidence["shares"]["operations"]["skipped_quota"])
            self.assertEqual(evidence["status"], "not_measured")

    def test_is_efs_mount(self):
        with tempfile.TemporaryDirectory() as d:
            mount = Path(d) / "mnt" / "efs"
            mount.mkdir(parents=True)
            mounts = Path(d) / "mounts"
            mounts.write_text(
                "fs-0123456789abcdef0.efs.us-west-2.amazonaws.com:/ "
                f"{mount} nfs4 rw 0 0\n", encoding="utf-8")
            self.assertTrue(migrate_efs.is_efs_mount(mount, mounts))
            mounts.write_text(
                f"/dev/sda1 {mount} ext4 rw 0 0\n", encoding="utf-8")
            self.assertFalse(migrate_efs.is_efs_mount(mount, mounts))
            # Missing mounts file: not EFS, no crash.
            self.assertFalse(migrate_efs.is_efs_mount(
                mount, Path(d) / "no-such-file"))


if __name__ == "__main__":
    unittest.main()
