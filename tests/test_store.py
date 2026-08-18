from __future__ import annotations

import base64
import json
import tempfile
import unittest
import zlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from release_notifier.errors import QueueConflictError, StoreError, UnsupportedSchemaError
from release_notifier.model import CommentTarget
from release_notifier.store import DESCRIPTION_PREFIX, StateStore

from tests.support import request


class StateStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temporary_directory.name) / "state.json"
        self.store = StateStore.initialize(self.state_file)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def target(release, kind: str = "issue", number: int = 2) -> CommentTarget:
        marker_kind = "pull-request" if kind == "pull_request" else "issue"
        return CommentTarget(
            kind,
            number,
            "Released",
            f"release-notifier:v1:{release.request_key}:{marker_kind}:{number}",
        )

    def test_multiple_releases_keep_metadata_and_order_after_reload(self) -> None:
        first = request("1.0.0", "a" * 40, "b" * 40)
        second = request("1.0.1", "b" * 40, "c" * 40)

        self.assertTrue(self.store.enqueue(first))
        self.assertTrue(self.store.enqueue(second))
        restarted = StateStore(self.state_file)

        state = restarted.inspect()[0]
        self.assertEqual("a" * 40, state["checkpoint"])
        self.assertEqual(["1.0.0", "1.0.1"], [item["version"] for item in state["requests"]])
        self.assertEqual("a" * 40, state["requests"][0]["baseCommit"])
        self.assertEqual("b" * 40, state["requests"][1]["baseCommit"])

    def test_enqueue_is_idempotent_and_conflicting_payload_is_rejected(self) -> None:
        release = request()
        self.assertTrue(self.store.enqueue(release))
        self.assertFalse(self.store.enqueue(release))

        with self.assertRaisesRegex(QueueConflictError, "different release data"):
            self.store.enqueue(replace(release, base_commit="c" * 40))

    def test_later_release_cannot_skip_a_commit_range(self) -> None:
        self.store.enqueue(request("1.0.0", "a" * 40, "b" * 40))

        with self.assertRaisesRegex(QueueConflictError, "expected base"):
            self.store.enqueue(request("1.0.2", "c" * 40, "d" * 40))

        self.assertEqual(1, len(self.store.inspect()[0]["requests"]))

    def test_checkpoint_advances_only_after_all_targets(self) -> None:
        release = request()
        target = self.target(release)
        self.store.enqueue(release)
        self.store.set_targets(release, (target,))

        with self.assertRaisesRegex(StoreError, "pending deliveries"):
            self.store.complete(release)
        self.assertEqual("a" * 40, self.store.inspect()[0]["checkpoint"])

        self.store.mark_delivery(release, target, "created")
        self.store.complete(release)

        state = self.store.inspect()[0]
        self.assertEqual("b" * 40, state["checkpoint"])
        self.assertEqual([], state["requests"])
        self.assertFalse(self.store.enqueue(release))

    def test_targets_and_partial_delivery_survive_reload(self) -> None:
        release = request()
        first = self.target(release, "pull_request", 1)
        second = self.target(release, "issue", 2)
        self.store.enqueue(release)
        self.store.set_targets(release, (first, second))
        self.store.mark_delivery(release, first, "created")
        self.store.fail(release, "GitHub unavailable\ntry later")

        restarted = StateStore(self.state_file)
        pending = restarted.next_request(release.repository, release.channel)

        self.assertEqual((first, second), pending.targets)
        self.assertEqual(
            ["created", "pending"],
            [item.status for item in restarted.deliveries(release)],
        )
        self.assertEqual(
            "GitHub unavailable try later",
            restarted.inspect()[0]["requests"][0]["lastError"],
        )

    def test_state_file_is_json_and_contains_no_worker_credential(self) -> None:
        release = request()
        self.store.enqueue(release)

        value = json.loads(self.state_file.read_text(encoding="utf-8"))

        self.assertEqual(1, value["schemaVersion"])
        self.assertEqual(release.version, value["channels"][0]["requests"][0]["request"]["version"])
        self.assertNotIn("worker-super-secret-token", self.state_file.read_text(encoding="utf-8"))

    def test_build_description_restores_pending_work(self) -> None:
        first = request("1.0.0", "a" * 40, "b" * 40)
        second = request("1.0.1", "b" * 40, "c" * 40)
        target = self.target(first)
        self.store.enqueue(first)
        self.store.enqueue(second)
        self.store.set_targets(first, (target,))
        self.store.mark_delivery(first, target, "created")
        self.store.fail(first, "temporary outage")

        description = self.store.describe()
        restored_path = Path(self.temporary_directory.name) / "restored.json"
        restored = StateStore.restore_description(restored_path, description)

        self.assertTrue(description.startswith(DESCRIPTION_PREFIX))
        self.assertNotIn("worker-super-secret-token", description)
        self.assertEqual(
            ["1.0.0", "1.0.1"],
            [item["version"] for item in restored.inspect()[0]["requests"]],
        )
        self.assertEqual("created", restored.deliveries(first)[0].status)
        restored.complete(first)
        self.assertEqual("b" * 40, restored.inspect()[0]["checkpoint"])

    def test_invalid_description_does_not_replace_state_file(self) -> None:
        self.store.enqueue(request())
        before = self.state_file.read_bytes()

        with self.assertRaisesRegex(StoreError, "unreadable"):
            StateStore.restore_description(
                self.state_file, f"{DESCRIPTION_PREFIX}not-base64!"
            )

        self.assertEqual(before, self.state_file.read_bytes())

    def test_unknown_state_schema_is_rejected_without_replacement(self) -> None:
        value = {"schemaVersion": 99, "channels": []}
        self.state_file.write_text(json.dumps(value), encoding="utf-8")
        before = self.state_file.read_bytes()

        with self.assertRaises(UnsupportedSchemaError):
            StateStore.initialize(self.state_file)

        self.assertEqual(before, self.state_file.read_bytes())

        description = DESCRIPTION_PREFIX + base64.urlsafe_b64encode(
            zlib.compress(json.dumps(value).encode("utf-8"))
        ).decode("ascii")
        with self.assertRaises(UnsupportedSchemaError):
            StateStore.restore_description(
                Path(self.temporary_directory.name) / "unknown.json", description
            )

    def test_description_export_rejects_oversized_state(self) -> None:
        self.store.enqueue(request())

        with patch("release_notifier.store.MAX_STATE_JSON_BYTES", 10):
            with self.assertRaisesRegex(StoreError, "payload is too large"):
                self.store.describe()

    def test_target_marker_must_match_request(self) -> None:
        release = request()
        self.store.enqueue(release)

        with self.assertRaisesRegex(StoreError, "invalid idempotency marker"):
            self.store.set_targets(
                release,
                (CommentTarget("issue", 2, "Released", "wrong-marker"),),
            )


if __name__ == "__main__":
    unittest.main()
