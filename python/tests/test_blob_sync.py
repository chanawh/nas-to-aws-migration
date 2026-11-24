"""Tests for the Azure Blob sync/verify engine, live against Azurite.

Run with Azurite started first, from python/ with the Azure venv:
    bash ../scripts/start_azurite.sh
    ~/workspace/nas-to-aws-migration-venv-azure/bin/python -m unittest discover -s tests -p "test_blob_sync.py" -v
No Docker, no Azure account. Azurite emulates the Blob API locally.
If Azurite is not answering, the module raises instead of silently
skipping - a green run must mean a live run.
"""
import hashlib
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from azure.core.exceptions import HttpResponseError
    from azure.storage.blob import BlobServiceClient

    from azure_blob.blob_sync import (
        DEFAULT_BLOB_ENDPOINT, load_manifest, make_blob_service_client,
        sync_all, sync_one_share,
    )
    from azure_blob.blob_verify import verify
    HAS_AZURE = True
except ImportError:
    # azure-storage-blob is installed in the Azure venv only. The S3
    # venv runs `make test` without it; the class below skips there
    # and the Blob suite runs under `make test-blob` instead. (If the
    # SDK is present but Azurite is not answering, the module still
    # raises instead of silently skipping - a green run must mean a
    # live run.)
    HAS_AZURE = False
from migrate_s3.dataset import generate_dataset

SHARES = ["finance", "it-support", "it-operations", "operations"]


def _make_source(tmp: Path) -> Path:
    source = tmp / "source"
    generate_dataset(source)
    return source


def _azurite_or_raise() -> "BlobServiceClient":
    service = make_blob_service_client()
    try:
        # One cheap call proves Azurite is answering.
        next(service.list_containers(), None)
    except Exception as e:
        raise RuntimeError(
            "Azurite is not answering on " + DEFAULT_BLOB_ENDPOINT +
            " - start it first: bash scripts/start_azurite.sh"
        ) from e
    return service


@unittest.skipUnless(HAS_AZURE, "azure-storage-blob not installed (use make test-blob)")
class TestBlobSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = _azurite_or_raise()
        cls.prefix = "nas2azure-test-" + uuid.uuid4().hex[:8]

    @classmethod
    def tearDownClass(cls):
        for share in SHARES:
            try:
                cls.service.delete_container(f"{cls.prefix}-{share}")
            except HttpResponseError:
                pass

    def setUp(self):
        # Fresh containers per test: delete any leftovers, recreate.
        for share in SHARES:
            try:
                self.service.delete_container(f"{self.prefix}-{share}")
            except HttpResponseError:
                pass
            self.service.create_container(f"{self.prefix}-{share}")

    def _container(self, share):
        return f"{self.prefix}-{share}"

    def test_initial_sync_uploads_everything_and_verify_passes(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            results = sync_all(self.service, source, self.prefix)
            self.assertEqual(sum(len(r.copied) for r in results.values()), 24)
            for share in SHARES:
                self.assertTrue(
                    verify(self.service, source / share, self._container(share)).passed)

    def test_second_sync_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.service, source, self.prefix)
            results = sync_all(self.service, source, self.prefix)
            self.assertEqual(sum(len(r.copied) for r in results.values()), 0)
            self.assertEqual(sum(len(r.skipped_unchanged) for r in results.values()), 24)

    def test_changed_file_is_reuploaded_and_unchanged_are_not(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.service, source, self.prefix)
            (source / "finance" / "budget-2027-draft.csv").write_text("changed\n", encoding="utf-8")
            results = sync_all(self.service, source, self.prefix)
            self.assertEqual(results["finance"].copied, ["budget-2027-draft.csv"])
            self.assertTrue(
                verify(self.service, source / "finance", self._container("finance")).passed)

    def test_corrupted_blob_is_detected_and_repaired(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.service, source, self.prefix)
            bc = self.service.get_blob_client(
                self._container("finance"), "budget-2027-draft.csv")
            bc.upload_blob(b"corrupted\n", overwrite=True)
            result = verify(self.service, source / "finance", self._container("finance"))
            self.assertFalse(result.passed)
            self.assertEqual(result.mismatched, ["budget-2027-draft.csv"])
            sync_all(self.service, source, self.prefix)
            self.assertTrue(
                verify(self.service, source / "finance", self._container("finance")).passed)

    def test_dry_run_uploads_nothing_and_writes_no_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            results = sync_all(self.service, source, self.prefix, dry_run=True)
            self.assertEqual(sum(len(r.copied) for r in results.values()), 24)
            self.assertEqual(load_manifest(self.service, self._container("finance")), {})
            blobs = list(self.service.get_container_client(
                self._container("finance")).list_blobs())
            self.assertEqual(blobs, [])

    def test_over_quota_share_is_skipped_whole(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            result = sync_one_share(
                self.service, source / "finance", self._container("finance"),
                quota_bytes=10)
            self.assertEqual(len(result.skipped_quota), 6)
            self.assertEqual(len(result.copied), 0)

    def test_manifest_is_stored_as_blob(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.service, source, self.prefix)
            manifest = load_manifest(self.service, self._container("finance"))
            self.assertEqual(len(manifest), 6)

    def test_large_file_uses_staged_blocks_and_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            big = source / "finance" / "big-9mb.bin"
            big.write_bytes(b"A" * (9 * 1024 * 1024))
            sync_all(self.service, source, self.prefix)
            bc = self.service.get_blob_client(self._container("finance"), "big-9mb.bin")
            body = bc.download_blob().readall()
            self.assertEqual(
                hashlib.sha256(body).hexdigest(),
                bc.get_blob_properties().metadata["sha256"])
            self.assertEqual(len(body), 9 * 1024 * 1024)
            committed, uncommitted = bc.get_block_list()
            self.assertEqual(len(committed), 2)
            self.assertEqual(uncommitted, [])
            self.assertTrue(
                verify(self.service, source / "finance", self._container("finance")).passed)

    def test_delete_removes_orphan_only_with_flag(self):
        with tempfile.TemporaryDirectory() as d:
            source = _make_source(Path(d))
            sync_all(self.service, source, self.prefix)
            (source / "finance" / "budget-2027-draft.csv").unlink()
            sync_all(self.service, source, self.prefix)
            names = [b.name for b in self.service.get_container_client(
                self._container("finance")).list_blobs()]
            self.assertIn("budget-2027-draft.csv", names)
            sync_all(self.service, source, self.prefix, delete=True)
            names = [b.name for b in self.service.get_container_client(
                self._container("finance")).list_blobs()]
            self.assertNotIn("budget-2027-draft.csv", names)


if __name__ == "__main__":
    unittest.main()
