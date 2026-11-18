"""Deterministic per-file Live Photo planning. No transport or format conversion."""
import hashlib
import json
import os
import re
from pathlib import Path
from .exceptions import ProtocolError
from .media_identity import apple_id
from .motion_photo import has_embedded_motion


def inspect_resource(path, video=False):
    identifier = apple_id(path, video) or ""
    embedded = not video and has_embedded_motion(path)
    hint = False
    if not video and not embedded:
        with open(path, "rb") as source:
            prefix = source.read(1024 * 1024)
        hint = b"MotionPhoto" in prefix or b"MicroVideo" in prefix
    return {"identifier": identifier, "embedded_motion": embedded, "motion_hint": hint}


def plan_live(value):
    requested = value.get("strategy", "automatic")
    resources = value.get("resources")
    if requested not in ("automatic", "original", "stepwise", "motion") or not isinstance(resources, list) or not 1 <= len(resources) <= 16:
        raise ProtocolError("INVALID_LIVE_PLAN")
    normalized = []
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get("id"), str) or not resource["id"] or type(resource.get("video")) is not bool:
            raise ProtocolError("INVALID_LIVE_PLAN")
        if type(resource.get("size")) is not int or resource["size"] <= 0:
            raise ProtocolError("INVALID_LIVE_PLAN")
        identifier = resource.get("identifier", "")
        if not isinstance(identifier, str) or (identifier and not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identifier)):
            raise ProtocolError("INVALID_LIVE_PLAN")
        normalized.append(resource)
    if len({r["id"] for r in normalized}) != len(normalized):
        raise ProtocolError("INVALID_LIVE_PLAN")
    groups = {}
    for resource in normalized:
        group = resource.get("group", "original")
        if not isinstance(group, str) or not group or len(group) > 64:
            raise ProtocolError("INVALID_LIVE_PLAN")
        groups.setdefault(group, []).append(resource)
    pairs = []
    for members in groups.values():
        photos = [r for r in members if not r["video"]]
        videos = [r for r in members if r["video"]]
        if len(photos) != 1 or len(videos) != 1:
            break
        photo, video = photos[0], videos[0]
        p, v = photo.get("identifier", ""), video.get("identifier", "")
        if (p and v and p != v) or not ((p and p == v) or value.get("system_pair") is True):
            break
        pairs.append({"photo": photo["id"], "video": video["id"]})
    else:
        strategy = "original" if requested == "automatic" else requested
        return _result(requested, "apple_pair", strategy, "", normalized, pairs)
    warning = ""
    if value.get("system_pair") is True or any(r.get("identifier") for r in normalized):
        warning = "LIVE_PAIR_AMBIGUOUS" if value.get("ambiguous") is True else "LIVE_PAIR_INCOMPLETE"
    if any(r.get("motion_hint") and not r.get("embedded_motion") for r in normalized):
        warning = "MOTION_STRUCTURE_UNKNOWN"
    embedded = [r for r in normalized if r.get("embedded_motion") is True and not r["video"]]
    if embedded and len(embedded) == len(normalized):
        return _result(requested, "motion_photo", "raw", warning, normalized, [])
    if embedded and len(embedded) != len(normalized):
        warning = warning or "LIVE_MIXED_REPRESENTATIONS"
    kind = "video" if all(r["video"] for r in normalized) else "photo" if all(not r["video"] for r in normalized) and not warning else "raw_resources"
    return _result(requested, kind, "raw", warning, normalized, [])


def _result(requested, kind, strategy, warning, resources, pairs):
    sources = [{key: r[key] for key in ("id", "size", "modified", "sha256") if key in r} for r in resources]
    sources.sort(key=lambda r: r["id"])
    return {"version": 1, "requested": requested, "kind": kind, "strategy": strategy, "warning": warning,
            "sources": sources, "pairs": sorted(pairs, key=lambda p: p["photo"]), "source_key": hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def freeze_pair_route(path, photo_hash, video_hash, photo_exists):
    """Choose before committing; retries never switch routes after an uncertain write."""
    path = Path(path)
    expected = {"photo": photo_hash, "video": video_hash}
    value = {**expected, "route": "reconcile" if photo_exists else "joint"}
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True); output.flush(); os.fsync(output.fileno())
    except FileExistsError:
        try:
            if path.stat().st_size > 2048:
                raise ProtocolError("INVALID_LIVE_PLAN")
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise ProtocolError("LIVE_PLAN_PENDING") from None
        if not isinstance(value, dict):
            raise ProtocolError("INVALID_LIVE_PLAN")
        if any(value.get(key) != digest for key, digest in expected.items()):
            raise ProtocolError("SOURCE_CHANGED")
        if value.get("route") not in ("joint", "reconcile"):
            raise ProtocolError("INVALID_LIVE_PLAN")
    return value["route"]
