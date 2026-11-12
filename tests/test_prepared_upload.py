"""Offline regression tests for per-file checks on the actual bundled uploader."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

from gpmc.client import Client
from rich.progress import Progress


class PreparedUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "photo.JPG"
        self.path.write_bytes(b"original")
        self.stat = self.path.stat()
        self.client = Client.__new__(Client)
        self.client.logger = MagicMock()
        self.client._log_failed_or_skipped = MagicMock()
        self.client.api = MagicMock()
        self.client.api.find_remote_media_by_hash.return_value = "existing-media-key"
        self.client.prepared_files = {
            os.path.normcase(os.path.abspath(self.path)): {
                "size": self.stat.st_size,
                "modified_ms": self.stat.st_mtime_ns // 1_000_000,
                "sha256": hashlib.sha256(b"original").hexdigest(),
            }
        }

    def upload(self, callback=None):
        with Progress(disable=True) as progress:
            return self.client._upload_file(
                self.path, None, progress, False, False, False,
                progress_callback=callback, auto_retries=0,
            )

    def test_unchanged_file_passes_and_uses_existing_remote_duplicate(self):
        self.assertEqual(self.upload(), {self.path.absolute().as_posix(): "existing-media-key"})
        self.client.api.find_remote_media_by_hash.assert_called_once()

    def test_size_change_is_rejected_before_network(self):
        self.path.write_bytes(b"different-size")
        with self.assertRaisesRegex(ValueError, "changed after preparation"):
            self.upload()
        self.assertEqual(self.client.api.mock_calls, [])

    def test_same_size_change_with_restored_timestamp_is_rejected(self):
        self.path.write_bytes(b"replaced")
        os.utime(self.path, ns=(self.stat.st_atime_ns, self.stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "content no longer matches"):
            self.upload()
        self.assertEqual(self.client.api.mock_calls, [])

    def test_missing_file_is_rejected(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.upload()
        self.assertEqual(self.client.api.mock_calls, [])

    def test_unselected_file_is_rejected(self):
        self.client.prepared_files = {}
        with self.assertRaisesRegex(ValueError, "not in the prepared upload plan"):
            self.upload()
        self.assertEqual(self.client.api.mock_calls, [])

    def test_change_after_hashing_is_rejected_before_upload(self):
        def change_after_lookup(_hash):
            self.path.write_bytes(b"changed after lookup")
            return None

        self.client.api.find_remote_media_by_hash.side_effect = change_after_lookup
        with self.assertRaisesRegex(ValueError, "changed after preparation"):
            self.upload()
        self.client.api.upload_file.assert_not_called()
        self.client.api.commit_upload.assert_not_called()



class AlbumFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.client = Client.__new__(Client)
        self.client._handle_target_input = MagicMock(return_value={Path("photo.JPG"): None})
        self.client._upload_concurrently = MagicMock(return_value={"photo.JPG": "media-key"})
        self.client.add_to_album = MagicMock()
        self.client.add_to_existing_album = MagicMock()

    def test_upload_reports_album_stage_after_file_processing_and_before_return(self):
        events = []

        def organize(keys, name, **kwargs):
            self.assertEqual(events[-1]["phase"], "archiving")
            self.assertEqual(events[-1]["phase_processed"], 0)

        self.client.add_to_album.side_effect = organize
        result = self.client.upload(["photo.JPG"], album_name="Photos", progress_callback=events.append)

        self.assertEqual(result, {"photo.JPG": "media-key"})
        self.assertEqual([event["phase_processed"] for event in events], [0, 1])
        self.assertTrue(all(event["phase_total"] == 1 for event in events))

    def test_auto_albums_report_each_group_including_remote_duplicates(self):
        updates = []
        self.client._handle_album_creation(
            {"2025-01/photo.JPG": "existing-key", "2025-02/video.MOV": "uploaded-key"},
            "AUTO", False, lambda done, total: updates.append((done, total)),
        )

        self.assertEqual(updates, [(0, 2), (1, 2), (2, 2)])
        self.assertEqual(self.client.add_to_album.call_count, 2)

    def test_failed_album_operation_does_not_report_finalization_as_complete(self):
        events = []
        self.client.add_to_album.side_effect = RuntimeError("album request failed")

        with self.assertRaisesRegex(RuntimeError, "album request failed"):
            self.client.upload(["photo.JPG"], album_name="Photos", progress_callback=events.append)

        self.assertEqual([event["phase_processed"] for event in events], [0])

    def test_no_successful_files_does_not_start_album_finalization(self):
        events = []
        self.client._upload_concurrently.return_value = {}

        self.assertEqual(self.client.upload(["photo.JPG"], album_name="Photos", progress_callback=events.append), {})
        self.client.add_to_album.assert_not_called()
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
