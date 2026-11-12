"""Offline boundaries of the shared protocol, independent of any client checkout."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import MagicMock

import requests
from blackboxprotobuf import decode_message

from gpmc.api import Api
from gpmc.errors import classify
from gpmc.exceptions import ProtocolError
from gpmc.live import blob, integer, commit_pair, motion_key
from gpmc.media_access import allowed_url, original_access
from gpmc.protocol import Protocol


class UnifiedCoreTests(unittest.TestCase):
    def test_bearer_refresh_is_serialized_across_threads(self):
        api = Api("unused")
        def refresh():
            time.sleep(.02)
            return {"Auth": "test-token", "Expiry": str(int(time.time()) + 3600)}
        api._get_auth_token = MagicMock(side_effect=refresh)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(lambda _: api.bearer_token, range(8))), ["test-token"] * 8)
        api._get_auth_token.assert_called_once()

    def test_proxy_keeps_tls_and_ignores_ambient_proxy_settings(self):
        with Api("unused", proxy="http://127.0.0.1:12345")._new_session() as session:
            self.assertTrue(session.verify)
            self.assertFalse(session.trust_env)
            self.assertEqual(session.proxies["https"], "http://127.0.0.1:12345")

    def test_error_classification_never_exposes_unrecognized_exception_text(self):
        self.assertEqual(classify(RuntimeError("token=private")), "ENGINE_ERROR")
        response = requests.Response()
        response.status_code = 429
        self.assertEqual(classify(requests.HTTPError("private URL", response=response)), "RATE_LIMITED")

    def protocol(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        api = MagicMock()
        api.find_remote_media_by_hash.return_value = "cloud-key"
        return Protocol(SimpleNamespace(api=api, staging=Path(temp.name)))

    def value(self):
        return {"sha1": hashlib.sha1(b"photo").hexdigest(), "sha256": hashlib.sha256(b"photo").hexdigest(), "size": 5}

    def test_prepare_duplicate_has_no_transport_credentials_or_upload(self):
        protocol = self.protocol()
        self.assertEqual(protocol.dispatch("prepare_upload", self.value()), {"exists": True, "remote_key": "cloud-key"})
        protocol.api.get_upload_token.assert_not_called()

    def test_cleanup_requires_fresh_evidence_and_boolean_policy(self):
        protocol = self.protocol()
        self.assertEqual(protocol.dispatch("check_cleanup", self.value())["reason"], "CLOUD_STATE_EXPIRED")
        with self.assertRaisesRegex(ProtocolError, "INVALID_CHECK_POLICY"):
            protocol.dispatch("check_cleanup", {**self.value(), "cloud_state": "false"})

    def test_failed_pagination_discards_partial_snapshot(self):
        protocol = self.protocol()
        protocol.snapshot, protocol.snapshot_at, protocol.sync_token = {"old": {}}, 100, "old-token"
        page = {"1": {"1": "repeated-page", "6": "new-token"}}
        protocol.api.get_library_state.return_value = page
        protocol.api.get_library_page.return_value = page
        with self.assertRaisesRegex(ProtocolError, "SYNC_CYCLE"):
            protocol.synchronize()
        self.assertEqual((protocol.snapshot, protocol.snapshot_at, protocol.sync_token), ({}, 0, ""))

    def test_original_url_rejects_bad_ports_credentials_and_lookalikes(self):
        for url in ("https://google.com:invalid/a", "https://google.com:444/a", "https://google.com.evil.test/a", "https://user@google.com/a"):
            self.assertFalse(allowed_url(url))
        self.assertTrue(allowed_url("https://lh3.googleusercontent.com/photo"))

    def test_original_access_501_falls_back_to_one_byte_get_without_body_read(self):
        api = MagicMock()
        api.get_download_urls.return_value = {"1": {"5": {"2": {"6": "https://lh3.googleusercontent.com/photo"}}}}
        session = api._new_session.return_value.__enter__.return_value
        responses = [MagicMock(status_code=501, headers={}), MagicMock(status_code=206, headers={"Content-Range": "bytes 0-0/5"})]
        for response in responses:
            response.__enter__.return_value = response
        session.request.side_effect = responses
        self.assertTrue(original_access(api, "cloud-key", 5))
        self.assertEqual(session.request.call_args.args[0], "GET")
        self.assertEqual(session.request.call_args.kwargs["headers"]["Range"], "bytes=0-0")
        for response in responses:
            response.iter_content.assert_not_called()

    def test_shared_pair_commit_preserves_receipts_and_modified_time(self):
        api = SimpleNamespace(bearer_token="test", user_agent="test", language="en_US", timeout=1, android_api_version=28)
        session = MagicMock()
        session.__enter__.return_value = session
        api._new_session = lambda: session
        response = MagicMock(status_code=200, content=blob(1, blob(3, blob(1, b"linked"))))
        session.post.return_value = response
        receipt = integer(1, 2) + blob(2, b"opaque-token")
        self.assertEqual(commit_pair(api, "joint", "original", "photo.heic", 123, 456, b"p" * 20, b"v" * 20, receipt, receipt), "linked")
        body = decode_message(session.post.call_args.kwargs["data"])[0]
        self.assertEqual(body["1"]["6"]["1"], 456)
        self.assertIn("x-goog-ext-173412678-bin", session.post.call_args.kwargs["headers"])
        with self.assertRaisesRegex(ProtocolError, "UPLOAD_RESPONSE_INVALID"):
            commit_pair(api, "joint", "original", "photo.heic", 123, 456, b"p" * 20, b"v" * 20, b"invalid", receipt)
        session.post.reset_mock()
        response.content = blob(1, blob(2, blob(2, blob(1, b"linked"))))
        self.assertEqual(motion_key(api, b"v" * 20), "linked")
        self.assertNotIn("x-goog-ext-173412678-bin", session.post.call_args.kwargs["headers"])


if __name__ == "__main__":
    unittest.main()
