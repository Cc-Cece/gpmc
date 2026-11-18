"""Optional, bounded account presentation; never returns credentials or avatar URLs."""
import base64
import json
import re
from urllib.parse import urlparse
import requests


def account_profile(api, account, session_factory=requests.Session):
    def session():
        value = session_factory()
        value.trust_env = False
        value.verify = True
        value.proxies = {"http": api.proxy, "https": api.proxy}
        return value

    def bounded(response, limit):
        if response.status_code != 200:
            return None
        result = bytearray()
        for chunk in response.iter_content(8192):
            result.extend(chunk)
            if len(result) > limit:
                return None
        return bytes(result)

    try:
        with session() as client:
            with client.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": "Bearer " + api.bearer_token}, timeout=(3, 5), allow_redirects=False, stream=True) as response:
                body = bounded(response, 65536)
        profile = json.loads(body) if body else None
        if not isinstance(profile, dict):
            return {"available": False}
        subject = profile.get("sub")
        if not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", subject):
            return {"available": False}
        email = profile.get("email")
        if email is not None and (not isinstance(email, str) or email.strip().lower() != account.strip().lower()):
            return {"available": False}
        name = profile.get("name", "")
        name = name.strip() if isinstance(name, str) else ""
        if len(name) > 100 or any(ord(c) < 32 for c in name):
            name = ""
        result = {"available": True, "account": account, "subject": subject, "name": name}
        url = profile.get("picture", "")
        parsed = urlparse(url) if isinstance(url, str) else None
        if parsed and parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443) and parsed.hostname and (parsed.hostname == "googleusercontent.com" or parsed.hostname.endswith(".googleusercontent.com")):
            # A fresh session: bearer header and identity cookies never reach the image host.
            try:
                with session() as images:
                    with images.get(url, timeout=(3, 5), allow_redirects=False, stream=True) as response:
                        image = bounded(response, 512 * 1024) if response.headers.get("Content-Type", "").lower().startswith("image/") else None
                if image:
                    result["avatar_base64"] = base64.b64encode(image).decode("ascii")
            except requests.RequestException:
                pass
        return result
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return {"available": False}
