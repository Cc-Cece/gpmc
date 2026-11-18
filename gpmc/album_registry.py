"""Durable album assignment. An ambiguous create is reconciled, never blindly repeated."""
from pathlib import Path
from contextlib import closing
import hashlib
import sqlite3
import threading
import unicodedata
import requests
from .db_update_parser import parse_db_update, _get_nested
from .deadline import check as check_budget
from .exceptions import ProtocolError

_locks = [threading.RLock() for _ in range(64)]


def _title(value):
    return unicodedata.normalize("NFC", value.strip())


def _catalog(api, db, account):
    row = db.execute("SELECT token FROM album_catalog_state WHERE account=?", (account,)).fetchone()
    token = row[0] if row else ""
    ready = bool(token)
    values = dict(db.execute("SELECT remote_key,title FROM album_catalog WHERE account=?", (account,))) if ready else {}
    for _ in range(10):
        check_budget()
        page_token = token
        response = api.get_library_state(token if ready else "")
        more = _get_nested(response, "1", "7") == 2
        seen = set()
        while True:
            check_budget()
            new_token, resume, _, deleted = parse_db_update(response)
            token = new_token or token
            for key in deleted:
                values.pop(key, None)
            collections = _get_nested(response, "1", "3") or []
            if isinstance(collections, dict):
                collections = [collections]
            for collection in collections:
                key, title = collection.get("1"), _get_nested(collection, "2", "5")
                if isinstance(key, bytes): key = key.decode("utf-8", errors="replace")
                if isinstance(title, bytes): title = title.decode("utf-8", errors="replace")
                if isinstance(key, str) and key and isinstance(title, str):
                    values[key] = _title(title)
            if not resume:
                break
            if resume in seen:
                raise ProtocolError("SYNC_CYCLE")
            seen.add(resume)
            response = api.get_library_page(resume, page_token) if ready else api.get_library_page_init(resume)
        if not token:
            raise ProtocolError("SYNC_TOKEN_MISSING")
        if not more:
            with db:
                db.execute("DELETE FROM album_catalog WHERE account=?", (account,))
                db.executemany("INSERT INTO album_catalog VALUES(?,?,?)", [(account, key, title) for key, title in values.items()])
                db.execute("INSERT OR REPLACE INTO album_catalog_state VALUES(?,?)", (account, token))
            return values
        ready = True
    raise ProtocolError("SYNC_INCOMPLETE")


def ensure_album(api, storage, account, local_id, title, keys, legacy_key=""):
    if not isinstance(account, str) or "@" not in account or not isinstance(local_id, str) or not local_id or len(local_id) > 2048:
        raise ProtocolError("INVALID_ALBUM")
    if not isinstance(title, str) or not title.strip() or len(title) > 500 or not isinstance(keys, list) or not keys or any(not isinstance(k, str) or not k for k in keys):
        raise ProtocolError("INVALID_ALBUM")
    account, title = account.strip().lower(), _title(title)
    storage = Path(storage)
    storage.parent.mkdir(parents=True, exist_ok=True)
    lock = _locks[int.from_bytes(hashlib.sha256((str(storage.resolve()) + account).encode()).digest()[:2], "big") % len(_locks)]
    with lock, closing(sqlite3.connect(storage)) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS album_assignments(account TEXT,local_id TEXT,title TEXT,remote_key TEXT,state TEXT,PRIMARY KEY(account,local_id))")
        db.execute("CREATE TABLE IF NOT EXISTS album_catalog(account TEXT,remote_key TEXT,title TEXT,PRIMARY KEY(account,remote_key))")
        db.execute("CREATE TABLE IF NOT EXISTS album_catalog_state(account TEXT PRIMARY KEY,token TEXT)")
        db.commit()
        row = db.execute("SELECT remote_key,state FROM album_assignments WHERE account=? AND local_id=?", (account, local_id)).fetchone()
        remote = row[0] if row else ""
        if not remote and legacy_key:
            remote = legacy_key
        if not remote:
            known = db.execute("SELECT DISTINCT remote_key FROM album_assignments WHERE account=? AND title=? AND remote_key!=''", (account, title)).fetchall()
            if len(known) == 1:
                remote = known[0][0]
        if not remote:
            matches = [key for key, name in _catalog(api, db, account).items() if name == title]
            if len(matches) > 1:
                raise ProtocolError("ALBUM_NAME_AMBIGUOUS")
            if matches:
                remote = matches[0]
        if remote:
            with db:
                db.execute("INSERT OR REPLACE INTO album_assignments VALUES(?,?,?,?,?)", (account, local_id, title, remote, "confirmed"))
            api.add_media_to_album(remote, keys)
            return {"remote_key": remote, "created": False}
        pending = db.execute("SELECT 1 FROM album_assignments WHERE account=? AND title=? AND state='submitted'", (account, title)).fetchone()
        if pending:
            raise ProtocolError("ALBUM_CREATION_PENDING")
        # Commit the fence before a request that may succeed even if its response is lost.
        with db:
            db.execute("INSERT OR REPLACE INTO album_assignments VALUES(?,?,?,?,?)", (account, local_id, title, "", "submitted"))
        try:
            remote = api.create_album(title, keys)
        except requests.ConnectTimeout:
            with db:
                db.execute("UPDATE album_assignments SET state='retryable' WHERE account=? AND local_id=?", (account, local_id))
            raise
        if not isinstance(remote, str) or not remote:
            raise ProtocolError("ALBUM_CREATION_PENDING")
        with db:
            db.execute("UPDATE album_assignments SET remote_key=?,state='confirmed' WHERE account=? AND local_id=?", (remote, account, local_id))
        return {"remote_key": remote, "created": True}
