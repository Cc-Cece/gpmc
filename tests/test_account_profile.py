import json
import unittest
from types import SimpleNamespace
from gpmc.account_profile import account_profile


class Response:
    status_code = 200
    headers = {"Content-Type": "image/png"}
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): yield self.body


class ProfileTests(unittest.TestCase):
    def fetch(self, profile, image=b"image"):
        calls, sessions = [], []
        class Session:
            def __init__(self): sessions.append(self)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def get(self, url, **kwargs):
                calls.append((url, kwargs))
                return Response(json.dumps(profile).encode() if len(calls) == 1 else image)
        result = account_profile(SimpleNamespace(proxy="http://localhost:1234", bearer_token="secret"), "one@example.com", Session)
        return result, calls, sessions
    def test_proxy_and_separate_image_identity(self):
        result, calls, sessions = self.fetch({"sub": "123", "email": "one@example.com", "picture": "https://lh3.googleusercontent.com/avatar"})
        self.assertIn("avatar_base64", result)
        self.assertEqual(len(sessions), 2)
        for session in sessions:
            self.assertFalse(session.trust_env)
            self.assertEqual(session.proxies["https"], "http://localhost:1234")
        self.assertNotIn("headers", calls[1][1])
        self.assertFalse(calls[1][1]["allow_redirects"])
    def test_different_account_rejected(self):
        result, calls, _ = self.fetch({"sub": "123", "email": "other@example.com"})
        self.assertFalse(result["available"])
        self.assertEqual(len(calls), 1)
    def test_untrusted_image_and_oversized_image_ignored(self):
        result, calls, _ = self.fetch({"sub": "123", "picture": "https://evil.example/avatar"})
        self.assertEqual(len(calls), 1)
        self.assertNotIn("avatar_base64", result)
        result, _, _ = self.fetch({"sub": "123", "picture": "https://lh3.googleusercontent.com/avatar"}, b"x" * (512*1024+1))
        self.assertTrue(result["available"])
        self.assertNotIn("avatar_base64", result)
