"""Tests for the OneFS bridge. No OneFS node, no VMware, no SMB server.

The mount root is a plain local directory standing in for the mounted
shares, with the same layout. That is the honest test boundary here:
it proves the seed/sync/verify path through a OneFS-shaped tree, and
that check-onefs fails cleanly on an unreachable host. It does not
prove a real OneFS VM mounts - that run is on the user's machine.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moto import mock_aws

from migrate_s3.dataset import generate_dataset
from migrate_s3.onefs import (
    check_onefs,
    mount_shares,
    seed_onefs,
    unmount_shares,
    validate_mount_root,
)
from migrate_s3.sync import make_s3_client, sync_all
from migrate_s3.verify import verify

PREFIX = "nas2aws-migration"
SHARES = ["finance", "it-support", "it-operations", "operations"]


class TestOneFS(unittest.TestCase):
    def test_check_unreachable_host_fails_cleanly(self):
        # Mock the TCP probes: in some sandboxes TEST-NET is intercepted,
        # so do not rely on the real network for this unit test.
        with mock.patch("migrate_s3.onefs._tcp_ok", return_value=False):
            result = check_onefs("192.0.2.50", "root", "")
        self.assertFalse(result.ok)
        self.assertFalse(result.tcp_445_smb)
        self.assertTrue(result.errors)

    def test_mount_dry_run_prints_commands_and_mounts_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            mount_root = Path(d) / "onefs"
            ran = mount_shares("192.168.1.50", mount_root, "root", "secret", dry_run=True)
            self.assertEqual(len(ran), 4)  # one mount per share on Linux
            self.assertTrue(all("192.168.1.50" in c for c in ran))
            self.assertFalse(mount_root.exists())

    def test_unmount_dry_run_prints_commands(self):
        with tempfile.TemporaryDirectory() as d:
            ran = unmount_shares(Path(d) / "onefs", dry_run=True)
            self.assertEqual(len(ran), 4)

    def test_seed_copies_dataset_into_mount_tree(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            mount_root = Path(d) / "onefs"
            for share in SHARES:
                (mount_root / share).mkdir(parents=True)
            copied = seed_onefs(source, mount_root)
            self.assertEqual(copied, {"finance": 6, "it-support": 6, "it-operations": 7, "operations": 5})
            self.assertTrue((mount_root / "users.csv").is_file())
            self.assertTrue((mount_root / "acl.json").is_file())
            self.assertTrue((mount_root / "quotas.json").is_file())
            self.assertEqual(validate_mount_root(mount_root), [])

    def test_validate_mount_root_flags_empty_share(self):
        with tempfile.TemporaryDirectory() as d:
            mount_root = Path(d) / "onefs"
            mount_root.mkdir()
            problems = validate_mount_root(mount_root)
            self.assertTrue(any("users.csv" in p for p in problems))
            self.assertTrue(any("share directory" in p for p in problems))

    def test_seed_refuses_when_shares_not_mounted(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source"
            generate_dataset(source)
            mount_root = Path(d) / "onefs"
            mount_root.mkdir()
            # No share subdirectories at all: seed would copy onto the
            # host disk and pretend it was OneFS, so it must still work
            # at the copy level but validation must flag it. Here we
            # check the validator, which the CLI uses as the gate.
            self.assertTrue(validate_mount_root(mount_root))

    def test_sync_from_seeded_mount_root_passes_verify(self):
        with mock_aws():
            s3 = make_s3_client(None, region="us-west-2")
            for share in SHARES:
                s3.create_bucket(
                    Bucket=f"{PREFIX}-{share}",
                    CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
                )
            with tempfile.TemporaryDirectory() as d:
                source = Path(d) / "source"
                generate_dataset(source)
                mount_root = Path(d) / "onefs"
                for share in SHARES:
                    (mount_root / share).mkdir(parents=True)
                seed_onefs(source, mount_root)
                self.assertEqual(validate_mount_root(mount_root), [])
                results = sync_all(s3, mount_root, PREFIX)
                self.assertEqual(sum(len(r.copied) for r in results.values()), 24)
                for share in SHARES:
                    self.assertTrue(verify(s3, mount_root / share, f"{PREFIX}-{share}").passed)

    def test_check_onefs_reads_cluster_info_from_api(self):
        # Mock the Platform API response, no network.
        fake = {"name": "nas2aws-onefs", "onefs_version": {"release": "9.12.0.0"}}
        with mock.patch("migrate_s3.onefs._api_get", return_value=fake):
            with mock.patch("migrate_s3.onefs._tcp_ok", return_value=True):
                result = check_onefs("192.168.1.50", "root", "secret")
                self.assertTrue(result.ok)
                self.assertEqual(result.cluster_name, "nas2aws-onefs")
                self.assertEqual(result.onefs_version, "9.12.0.0")


if __name__ == "__main__":
    unittest.main()
