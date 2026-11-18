import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import requests
from gpmc.album_registry import ensure_album
from gpmc.exceptions import ProtocolError


class Api:
    def __init__(self):
        self.albums = {}
        self.created = 0
        self.fail = False
    def get_library_state(self, token):
        return {"1": {"6": "token", "3": [{"1": key, "2": {"5": title}} for key, title in self.albums.items()]}}
    def create_album(self, title, keys):
        self.created += 1
        key = str(self.created)
        if self.fail:
            self.albums[key] = title
            raise requests.ReadTimeout()
        self.albums[key] = title
        return key
    def add_media_to_album(self, key, keys):
        pass


class AlbumRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "albums.sqlite"
        self.api = Api()
    def ensure(self, local="folder", account="one@example.com"):
        return ensure_album(self.api, self.path, account, local, "Camera", ["media"])
    def test_repeat_and_restart_reuse(self):
        self.assertTrue(self.ensure()["created"])
        self.assertFalse(self.ensure()["created"])
        self.assertFalse(self.ensure("another")["created"])
        self.assertEqual(self.api.created, 1)
    def test_existing_remote_and_ambiguity(self):
        self.api.albums = {"old": "Camera"}
        self.assertEqual(self.ensure()["remote_key"], "old")
        self.api.albums["duplicate"] = "Camera"
        with self.assertRaises(ProtocolError): self.ensure("new", "two@example.com")
        self.assertEqual(self.api.created, 0)
    def test_lost_response_reconciles(self):
        self.api.fail = True
        with self.assertRaises(requests.ReadTimeout): self.ensure()
        self.assertEqual(self.ensure()["remote_key"], "1")
        self.assertEqual(self.api.created, 1)
    def test_unresolved_request_never_repeated_even_other_local_id(self):
        self.api.fail = True
        with self.assertRaises(requests.ReadTimeout): self.ensure()
        self.api.albums.clear()
        with self.assertRaises(ProtocolError): self.ensure()
        with self.assertRaises(ProtocolError): self.ensure("other")
        self.assertEqual(self.api.created, 1)
    def test_parallel_calls_create_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.ensure(), range(12)))
        self.assertEqual(self.api.created, 1)
        self.assertEqual({r["remote_key"] for r in results}, {"1"})
    def test_accounts_do_not_share_mappings(self):
        self.ensure()
        self.api.albums.clear()
        self.ensure(account="two@example.com")
        self.assertEqual(self.api.created, 2)
