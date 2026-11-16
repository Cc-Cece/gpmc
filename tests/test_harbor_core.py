"""Tests for the shared business core (gpmc.harbor_core).

Offline, no network, no platform code. Covers the frozen interface:
state machine, idempotent transitions, dedup, cleanup-guidance evidence,
retry backoff, JSON store + legacy migration, and log redaction.

Cleanup note: per PicHarbor-iOS docs/REQUIREMENTS.md (2026-10-19) local
cleanup belongs to Google Photos' official "free up space"; PicHarbor
performs no local deletion. decide_cleanup only judges whether the
cloud-confirmation evidence is sufficient to show the official-cleanup
guidance -- it never authorises a delete.
"""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from gpmc import harbor_core
from gpmc.harbor_core import (
    HarborCore,
    capabilities,
    create_task,
    decide_cleanup,
    decide_prepare,
    dispatch,
    dispatch_json,
    log_event,
    migrate_legacy,
    retry_delay,
    store_load,
    store_save,
    transition,
)


def asset(**overrides):
    value = {
        "local_id": "local-1",
        "filename": "IMG_0001.heic",
        "sha1": hashlib.sha1(b"photo").hexdigest(),
        "sha256": hashlib.sha256(b"photo").hexdigest(),
        "size": 5,
        "media_kind": "photo",
        "created_at": "2026-11-13T00:00:00+00:00",
    }
    value.update(overrides)
    return value


def task(**overrides):
    result = create_task("task-1", asset())
    assert result.get("task") is not None, result
    created = result["task"]
    created.update(overrides)
    return created


def fresh_evidence(**overrides):
    evidence = {"cloud_state": "confirmed_original",
                "checked_at": time.time(), "policy": True}
    evidence.update(overrides)
    return evidence


def drive(task_dict, *states, **kwargs):
    """Apply transitions in order; assert each succeeds; return the task."""
    current = task_dict
    for state in states:
        result = transition(current, state, **kwargs)
        assert result.get("ok") is not False, result
        current = result["task"]
    return current


class CreateTaskTests(unittest.TestCase):
    def test_defaults(self):
        result = create_task("task-1", asset(), idempotency_key="key-1")
        task_dict = result["task"]
        self.assertEqual(task_dict["state"], "queued")
        self.assertEqual(task_dict["attempts"], 0)
        self.assertIsNone(task_dict["error_code"])
        self.assertIsNone(task_dict["remote_key"])
        self.assertFalse(task_dict["cloud_confirmed"])
        self.assertEqual(task_dict["idempotency_key"], "key-1")
        self.assertEqual(task_dict["history"],
                         [{"state": "queued", "at": task_dict["created_at"],
                           "event": "created"}])

    def test_rejects_bad_digest(self):
        self.assertEqual(create_task("t", asset(sha1="xyz")),
                         {"ok": False, "error": "INVALID_DIGEST"})
        self.assertEqual(create_task("t", asset(size=0)),
                         {"ok": False, "error": "INVALID_DIGEST"})

    def test_rejects_bad_media_kind(self):
        self.assertEqual(create_task("t", asset(media_kind="raw")),
                         {"ok": False, "error": "INVALID_MEDIA_KIND"})

    def test_rejects_missing_task_id(self):
        self.assertEqual(create_task("", asset()),
                         {"ok": False, "error": "INVALID_ARGUMENT"})


class TransitionTests(unittest.TestCase):
    def test_happy_path(self):
        done = drive(task(), "preparing", "uploading", "submitting",
                     "confirming", "done")
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["attempts"], 1)
        self.assertTrue(done["cloud_confirmed"])
        self.assertEqual([h["state"] for h in done["history"]],
                         ["queued", "preparing", "uploading", "submitting",
                          "confirming", "done"])

    def test_illegal_transition(self):
        self.assertEqual(transition(task(), "done"),
                         {"ok": False, "error": "INVALID_TRANSITION"})
        self.assertEqual(transition(task(), "confirming"),
                         {"ok": False, "error": "INVALID_TRANSITION"})

    def test_done_is_terminal(self):
        done = drive(task(), "preparing", "uploading", "submitting",
                     "confirming", "done")
        for target in ("queued", "preparing", "cancelled", "failed"):
            self.assertEqual(transition(done, target),
                             {"ok": False, "error": "INVALID_TRANSITION"})

    def test_any_running_state_can_pause_cancel_or_fail(self):
        for state in ("preparing", "uploading", "submitting", "confirming"):
            base = drive(task(), "preparing")
            if state != "preparing":
                path = {"uploading": ("uploading",),
                        "submitting": ("uploading", "submitting"),
                        "confirming": ("uploading", "submitting", "confirming")}[state]
                base = drive(base, *path)
            for target in ("paused", "cancelled", "failed"):
                result = transition(base, target, error_code="NETWORK_ERROR")
                self.assertEqual(result["task"]["state"], target, (state, target))

    def test_failed_retry_cycle_counts_one_attempt_per_try(self):
        failed = drive(task(), "preparing", "uploading")
        failed = transition(failed, "failed", error_code="NETWORK_ERROR")["task"]
        self.assertEqual(failed["error_code"], "NETWORK_ERROR")
        queued = transition(failed, "queued")["task"]
        self.assertIsNone(queued["error_code"])
        self.assertEqual(queued["attempts"], 1)
        retry = transition(queued, "preparing")["task"]
        self.assertEqual(retry["attempts"], 2)

    def test_pause_resume_and_cancel(self):
        paused = drive(task(), "preparing", "uploading", "paused")
        resumed = drive(paused, "queued", "preparing")
        self.assertEqual(resumed["attempts"], 2)
        cancelled = transition(resumed, "cancelled")["task"]
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(transition(cancelled, "queued"),
                         {"ok": False, "error": "INVALID_TRANSITION"})

    def test_interrupted_resumes_via_queued(self):
        interrupted = drive(task(), "preparing", "uploading", "interrupted")
        self.assertEqual(transition(interrupted, "queued")["task"]["state"], "queued")

    def test_waiting_network_and_waiting_confirmation(self):
        waiting = drive(task(), "preparing", "uploading", "waiting_network")
        self.assertEqual(transition(waiting, "queued")["task"]["state"], "queued")
        confirming = drive(task(), "preparing", "uploading", "submitting",
                           "confirming", "waiting_confirmation")
        self.assertEqual(transition(confirming, "done")["task"]["state"], "done")


class IdempotencyTests(unittest.TestCase):
    def test_repeat_transition_to_same_state_is_noop(self):
        uploading = drive(task(), "preparing", "uploading")
        replay = transition(uploading, "uploading", idempotency_key="op-1")
        self.assertTrue(replay.get("idempotent_replay"))
        self.assertEqual(replay["task"], uploading)
        self.assertEqual(len(replay["task"]["history"]),
                         len(uploading["history"]))

    def test_repeat_submit_produces_no_duplicate_marker(self):
        submitting = drive(task(), "preparing", "uploading", "submitting")
        replay = transition(submitting, "submitting", idempotency_key="op-2")
        self.assertTrue(replay.get("idempotent_replay"))
        markers = [h for h in replay["task"]["history"]
                   if h["state"] == "submitting"]
        self.assertEqual(len(markers), 1)
        self.assertEqual(replay["task"]["attempts"], submitting["attempts"])

    def test_same_key_replay_after_retry_still_counts_new_attempt_once(self):
        # A genuine new attempt (failed -> queued -> preparing) counts once;
        # replaying that same preparing transition does not count again.
        failed = drive(task(), "preparing", "uploading")
        failed = transition(failed, "failed", error_code="NETWORK_ERROR")["task"]
        queued = transition(failed, "queued")["task"]
        first = transition(queued, "preparing", idempotency_key="op-3")["task"]
        self.assertEqual(first["attempts"], 2)
        replay = transition(first, "preparing", idempotency_key="op-3")
        self.assertTrue(replay.get("idempotent_replay"))
        self.assertEqual(replay["task"]["attempts"], 2)


class DedupTests(unittest.TestCase):
    def test_dedup_hit(self):
        result = decide_prepare(asset(), remote_key="cloud-key")
        self.assertEqual(result, {"exists": True, "remote_key": "cloud-key"})
        done = drive(task(), "preparing", "dedup_hit", "done")
        self.assertEqual(done["state"], "done")
        self.assertTrue(done["cloud_confirmed"])

    def test_dedup_miss(self):
        self.assertEqual(decide_prepare(asset()), {"exists": False})

    def test_dedup_rejects_bad_digest(self):
        self.assertEqual(decide_prepare(asset(sha256="abc")),
                         {"ok": False, "error": "INVALID_DIGEST"})


class CleanupGuidanceTests(unittest.TestCase):
    def test_eligible_when_cloud_confirmed_fresh_and_policy_allows(self):
        self.assertEqual(decide_cleanup(task(), fresh_evidence()),
                         {"eligible": True})

    def test_expired_evidence_never_grants_guidance(self):
        stale = fresh_evidence(checked_at=time.time() - 3601)
        self.assertEqual(decide_cleanup(task(), stale),
                         {"eligible": False, "reason": "CLOUD_STATE_EXPIRED"})

    def test_missing_evidence_fields_deny(self):
        self.assertEqual(decide_cleanup(task(), {}),
                         {"eligible": False, "reason": "CLOUD_STATE_EXPIRED"})
        self.assertEqual(decide_cleanup(task(), {"cloud_state": "confirmed_original"}),
                         {"eligible": False, "reason": "CLOUD_STATE_EXPIRED"})

    def test_future_checked_at_is_not_trusted(self):
        future = fresh_evidence(checked_at=time.time() + 600)
        self.assertEqual(decide_cleanup(task(), future),
                         {"eligible": False, "reason": "CLOUD_STATE_EXPIRED"})

    def test_non_bool_policy_is_an_error(self):
        self.assertEqual(decide_cleanup(task(), fresh_evidence(policy="yes")),
                         {"ok": False, "error": "INVALID_CHECK_POLICY"})
        self.assertEqual(decide_cleanup(task(), fresh_evidence(policy=None)),
                         {"ok": False, "error": "INVALID_CHECK_POLICY"})

    def test_policy_denied(self):
        self.assertEqual(decide_cleanup(task(), fresh_evidence(policy=False)),
                         {"eligible": False, "reason": "CLEANUP_GUIDANCE_DENIED"})

    def test_unconfirmed_cloud_state(self):
        self.assertEqual(decide_cleanup(task(), fresh_evidence(cloud_state="unconfirmed")),
                         {"eligible": False, "reason": "CLOUD_ORIGINAL_UNCONFIRMED"})

    def test_missing_remote(self):
        self.assertEqual(decide_cleanup(task(), fresh_evidence(cloud_state="missing")),
                         {"eligible": False, "reason": "REMOTE_MISSING"})

    def test_no_delete_capability_in_core(self):
        # The core must not expose any delete/remove action or helper.
        self.assertNotIn("delete", harbor_core.ACTIONS)
        self.assertFalse(any("delete" in name or "remove" in name
                             for name in dir(harbor_core)
                             if not name.startswith("_")))
        # An eligible result carries no delete instruction of any kind.
        self.assertEqual(decide_cleanup(task(), fresh_evidence()),
                         {"eligible": True})


class RetryDelayTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(retry_delay(0), {"delay_seconds": 5})
        self.assertEqual(retry_delay(1), {"delay_seconds": 10})
        self.assertEqual(retry_delay(2), {"delay_seconds": 20})
        self.assertEqual(retry_delay(5), {"delay_seconds": 160})
        self.assertEqual(retry_delay(6), {"delay_seconds": 300})
        self.assertEqual(retry_delay(100), {"delay_seconds": 300})

    def test_invalid_attempt(self):
        self.assertEqual(retry_delay(-1),
                         {"ok": False, "error": "INVALID_RETRY_ATTEMPT"})
        self.assertEqual(retry_delay("3"),
                         {"ok": False, "error": "INVALID_RETRY_ATTEMPT"})
        self.assertEqual(retry_delay(True),
                         {"ok": False, "error": "INVALID_RETRY_ATTEMPT"})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.json"

    def test_roundtrip(self):
        tasks = {"task-1": task(), "task-2": task(task_id="task-2")}
        saved = store_save(str(self.path), tasks)
        self.assertEqual(saved["saved"], True)
        self.assertEqual(saved["count"], 2)
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["version"], 1)
        self.assertEqual(set(on_disk["tasks"]), {"task-1", "task-2"})
        self.assertEqual(store_load(str(self.path)), {"tasks": tasks})
        leftovers = [p for p in Path(self.temp.name).iterdir()
                     if p.name.startswith(".harbor-store-")]
        self.assertEqual(leftovers, [])

    def test_save_accepts_task_list(self):
        self.assertTrue(store_save(str(self.path), [task()])["saved"])
        self.assertEqual(set(store_load(str(self.path))["tasks"]), {"task-1"})

    def test_load_missing_file(self):
        self.assertEqual(store_load(str(self.path)),
                         {"ok": False, "error": "FILE_ACCESS"})

    def test_load_bad_version(self):
        self.path.write_text(json.dumps({"version": 2, "tasks": {}}),
                             encoding="utf-8")
        self.assertEqual(store_load(str(self.path)),
                         {"ok": False, "error": "STORE_VERSION_UNSUPPORTED"})

    def test_load_corrupt(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(store_load(str(self.path)),
                         {"ok": False, "error": "STORE_CORRUPT"})

    def test_save_rejects_bad_task(self):
        bad = task()
        bad["state"] = "nope"
        self.assertEqual(store_save(str(self.path), {"task-1": bad}),
                         {"ok": False, "error": "INVALID_TASK"})


class MigrateLegacyTests(unittest.TestCase):
    def test_mapping(self):
        records = [
            {"task_id": "a", "state": "confirmed"},
            {"task_id": "b", "state": "confirmation_pending"},
            {"task_id": "c", "state": "uploading"},
            {"task_id": "d", "state": "interrupted"},
        ]
        result = migrate_legacy(records)["tasks"]
        self.assertEqual([t["state"] for t in result],
                         ["done", "waiting_confirmation", "interrupted",
                          "interrupted"])
        self.assertTrue(result[0]["cloud_confirmed"])
        self.assertIsNone(result[0]["error_code"])

    def test_unknown_legacy_state_fails_with_code(self):
        result = migrate_legacy([{"task_id": "x", "state": "archived"}])["tasks"]
        self.assertEqual(result[0]["state"], "failed")
        self.assertEqual(result[0]["error_code"], "UNKNOWN_LEGACY_STATE")

    def test_current_state_passes_through(self):
        result = migrate_legacy([{"task_id": "x", "state": "queued"}])["tasks"]
        self.assertEqual(result[0]["state"], "queued")

    def test_dict_input_and_defaults(self):
        result = migrate_legacy({"x": {"task_id": "x", "state": "confirmed"}})["tasks"]
        self.assertEqual(result[0]["state"], "done")
        self.assertEqual(result[0]["attempts"], 0)
        self.assertEqual(result[0]["history"][0]["event"], "migrated")


class LogEventTests(unittest.TestCase):
    def test_redacts_sensitive_keys(self):
        event = {"event": "upload.finished", "task_id": "task-1",
                 "auth_token": "secret-value", "password": "hunter2",
                 "nested": {"account_id": "user@example.com",
                            "refreshToken": "abc", "safe": "keep"},
                 "Authorization": "Bearer xyz"}
        redacted = log_event(event)
        self.assertEqual(redacted["auth_token"], "[redacted]")
        self.assertEqual(redacted["password"], "[redacted]")
        self.assertEqual(redacted["nested"]["account_id"], "[redacted]")
        self.assertEqual(redacted["nested"]["refreshToken"], "[redacted]")
        self.assertEqual(redacted["Authorization"], "[redacted]")
        self.assertEqual(redacted["nested"]["safe"], "keep")
        self.assertEqual(redacted["event"], "upload.finished")

    def test_redacts_credentials_and_secrets_in_lists(self):
        event = {"items": [{"api_secret": "s", "name": "n"},
                           {"credential": "c"}]}
        redacted = log_event(event)
        self.assertEqual(redacted["items"][0]["api_secret"], "[redacted]")
        self.assertEqual(redacted["items"][0]["name"], "n")
        self.assertEqual(redacted["items"][1]["credential"], "[redacted]")

    def test_drops_media_bodies_and_binary(self):
        event = {"event": "x", "media_body": b"bytes", "media_bytes": b"m",
                 "payload": b"raw", "filename": "IMG_0001.heic"}
        redacted = log_event(event)
        self.assertNotIn("media_body", redacted)
        self.assertNotIn("media_bytes", redacted)
        self.assertEqual(redacted["payload"], "[binary]")
        self.assertEqual(redacted["filename"], "IMG_0001.heic")

    def test_filename_reduced_to_basename(self):
        event = {"filename": "/sdcard/DCIM/IMG_0001.heic",
                 "file_path": "C:\\Users\\x\\DCIM\\IMG_0002.heic",
                 "asset": {"filename": "/a/b/c.jpg", "sha1": "d" * 40}}
        redacted = log_event(event)
        self.assertEqual(redacted["filename"], "IMG_0001.heic")
        self.assertEqual(redacted["file_path"], "IMG_0002.heic")
        self.assertEqual(redacted["asset"]["filename"], "c.jpg")
        self.assertNotIn("/sdcard", json.dumps(redacted))
        self.assertNotIn("C:\\Users", json.dumps(redacted))

    def test_sha_fields_never_written(self):
        sha1, sha256 = "a" * 40, "b" * 64
        event = {"event": "hash.done", "sha1": sha1, "sha256": sha256,
                 "asset": {"sha1": sha1, "sha256": sha256}}
        redacted = log_event(event)
        self.assertEqual(redacted["sha1"], "[redacted]")
        self.assertEqual(redacted["sha256"], "[redacted]")
        self.assertEqual(redacted["asset"]["sha1"], "[redacted]")
        self.assertNotIn(sha1, json.dumps(redacted))
        self.assertNotIn(sha256, json.dumps(redacted))

    def test_filename_with_email_is_redacted(self):
        self.assertEqual(log_event({"filename": "user@example.com.jpg"}),
                         {"filename": "[redacted]"})

    def test_long_filename_truncated(self):
        name = "x" * 200 + ".heic"
        redacted = log_event({"filename": "/a/" + name})
        self.assertEqual(len(redacted["filename"]), 128)

    def test_non_dict_event_is_an_error(self):
        self.assertEqual(log_event("nope"),
                         {"ok": False, "error": "INVALID_ARGUMENT"})


class DispatchTests(unittest.TestCase):
    def test_capabilities(self):
        result = capabilities()
        self.assertEqual(result["harbor_core_version"], 1)
        self.assertEqual(result["protocol_version"], 2)
        self.assertIn("done", result["states"])
        self.assertIn("create_task", result["actions"])
        wrapped = dispatch("capabilities", {})
        self.assertTrue(wrapped["ok"])
        self.assertEqual(wrapped["harbor_core_version"], 1)

    def test_dispatch_wraps_success_and_passes_errors(self):
        self.assertEqual(dispatch("retry_delay", {"attempt": 1}),
                         {"ok": True, "delay_seconds": 10})
        self.assertEqual(dispatch("retry_delay", {"attempt": -1}),
                         {"ok": False, "error": "INVALID_RETRY_ATTEMPT"})

    def test_dispatch_create_and_transition(self):
        created = dispatch("create_task", {"task_id": "t1", "asset": asset()})
        self.assertTrue(created["ok"])
        moved = dispatch("transition",
                         {"task": created["task"], "to": "preparing"})
        self.assertTrue(moved["ok"])
        self.assertEqual(moved["task"]["state"], "preparing")
        self.assertEqual(moved["task"]["attempts"], 1)

    def test_dispatch_decide_cleanup_guidance(self):
        result = dispatch("decide_cleanup",
                          {"task": task(), "evidence": fresh_evidence()})
        self.assertEqual(result, {"ok": True, "eligible": True})

    def test_dispatch_log_event(self):
        result = dispatch("log_event", {"event": {"token": "x", "n": 1}})
        self.assertEqual(result, {"ok": True, "event": {"token": "[redacted]", "n": 1}})

    def test_dispatch_unknown_action(self):
        self.assertEqual(dispatch("explode", {}),
                         {"ok": False, "error": "UNKNOWN_OPERATION"})

    def test_dispatch_never_raises(self):
        for action, value in (("transition", None), ("transition", "nope"),
                              ("create_task", None), ("retry_delay", {}),
                              ("store_load", {"path": None}),
                              ("decide_cleanup", {"task": {}, "evidence": {}})):
            result = dispatch(action, value)
            self.assertIsInstance(result, dict)
            self.assertIn("ok", result)

    def test_dispatch_json_roundtrip(self):
        payload = json.dumps({"task_id": "t1", "asset": asset()})
        result = json.loads(dispatch_json("create_task", payload))
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["state"], "queued")
        self.assertEqual(json.loads(dispatch_json("capabilities", "{}"))["ok"], True)
        self.assertEqual(json.loads(dispatch_json("retry_delay", "{bad")),
                         {"ok": False, "error": "INVALID_JSON"})
        self.assertEqual(json.loads(dispatch_json("retry_delay", "[1,2]")),
                         {"ok": False, "error": "INVALID_JSON"})

    def test_harbor_core_facade_matches_functions(self):
        core = HarborCore()
        self.assertEqual(core.retry_delay(2), retry_delay(2))
        self.assertEqual(core.capabilities(), capabilities())
        self.assertEqual(core.dispatch("retry_delay", {"attempt": 0}),
                         {"ok": True, "delay_seconds": 5})


if __name__ == "__main__":
    unittest.main()
