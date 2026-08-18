from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from release_notifier.errors import AmbiguousWriteError, TransientGitHubError
from release_notifier.github import ComparedRange, CompareCommit
from release_notifier.store import StateStore
from release_notifier.worker import process_all, process_channel

from tests.support import FakeGitHub, request


class WorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temporary_directory.name) / "state.json"
        self.store = StateStore.initialize(self.state_file)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_partial_ambiguous_delivery_retries_by_marker_without_duplicates(self) -> None:
        release = request()
        self.store.enqueue(release)
        github = FakeGitHub()
        github.add_release(release, pull_number=1, fixed_issue=2)

        def ambiguous_second(number: int, body: str) -> None:
            github.comments[number].append(body)
            if number == 2:
                raise AmbiguousWriteError("response lost")

        github.create_hook = ambiguous_second
        with self.assertRaises(AmbiguousWriteError):
            process_channel(self.store, github, release.repository, release.channel)

        failed = self.store.inspect()[0]
        self.assertEqual("a" * 40, failed["checkpoint"])
        self.assertEqual("delivering", failed["requests"][0]["status"])
        self.assertEqual(1, failed["requests"][0]["delivered"])

        github.create_hook = None
        completed = process_channel(self.store, github, release.repository, release.channel)

        self.assertEqual(1, len(completed))
        self.assertEqual([1, 2], github.create_attempts)
        self.assertEqual(1, len(github.comments[1]))
        self.assertEqual(1, len(github.comments[2]))
        state = self.store.inspect()[0]
        self.assertEqual("b" * 40, state["checkpoint"])
        self.assertEqual([], state["requests"])

    def test_multiple_releases_remain_ordered_during_github_outage(self) -> None:
        first = request("1.0.0", "a" * 40, "b" * 40)
        second = request("1.0.1", "b" * 40, "c" * 40)
        self.store.enqueue(first)
        self.store.enqueue(second)
        github = FakeGitHub()
        github.ranges[(first.base_commit, first.head_commit)] = TransientGitHubError("unavailable")
        github.add_release(second, pull_number=2)

        with self.assertRaises(TransientGitHubError):
            process_channel(self.store, github, first.repository, first.channel)

        state = self.store.inspect()[0]
        self.assertEqual(["queued", "queued"], [item["status"] for item in state["requests"]])
        self.assertEqual("a" * 40, state["checkpoint"])

        github.add_release(first, pull_number=1)
        completed = process_channel(self.store, github, first.repository, first.channel)

        self.assertEqual(["1.0.0", "1.0.1"], [item.version for item in completed])
        self.assertEqual([1, 2], github.create_attempts)
        self.assertEqual("c" * 40, self.store.inspect()[0]["checkpoint"])

    def test_existing_marker_counts_as_success(self) -> None:
        release = request()
        self.store.enqueue(release)
        github = FakeGitHub()
        github.add_release(release, pull_number=7)
        request_key = release.request_key
        github.comments[7].append(
            f"old visible text\n\n<!-- release-notifier:v1:{request_key}:pull-request:7 -->"
        )

        process_channel(self.store, github, release.repository, release.channel)

        self.assertEqual([], github.create_attempts)
        self.assertEqual("b" * 40, self.store.inspect()[0]["checkpoint"])

    def test_target_plan_is_not_rediscovered_after_partial_failure(self) -> None:
        release = request()
        self.store.enqueue(release)
        github = FakeGitHub()
        github.add_release(release, pull_number=1, fixed_issue=2)
        github.create_hook = lambda number, body: (_ for _ in ()).throw(
            TransientGitHubError("write failed")
        )
        with self.assertRaises(TransientGitHubError):
            process_channel(self.store, github, release.repository, release.channel)

        self.assertEqual(2, self.store.inspect()[0]["requests"][0]["targets"])

        github.pulls[release.head_commit] = ()
        github.closing.clear()
        github.issues.clear()
        github.create_hook = None
        process_channel(self.store, github, release.repository, release.channel)

        self.assertEqual([1, 1, 2], github.create_attempts)
        self.assertEqual([], self.store.inspect()[0]["requests"])

    def test_no_targets_completes_and_advances_checkpoint(self) -> None:
        release = request(base="a" * 40, head="a" * 40)
        self.store.enqueue(release)
        github = FakeGitHub()
        github.ranges[(release.base_commit, release.head_commit)] = ComparedRange("identical", ())

        completed = process_channel(self.store, github, release.repository, release.channel)

        self.assertEqual(0, completed[0].targets)
        self.assertEqual([], self.store.inspect()[0]["requests"])

    def test_unrelated_channels_continue_when_one_fails(self) -> None:
        broken = request(repository="mezz/Broken", channel="main")
        healthy = request(
            base="c" * 40,
            head="d" * 40,
            repository="mezz/Healthy",
            channel="main",
        )
        self.store.enqueue(broken)
        self.store.enqueue(healthy)
        github = FakeGitHub()
        github.ranges[(broken.base_commit, broken.head_commit)] = TransientGitHubError("outage")
        github.add_release(healthy, pull_number=8)

        completed, failures = process_all(self.store, github)

        self.assertEqual([healthy.request_key], [item.request_key for item in completed])
        self.assertEqual([broken.request_key], [item.request_key for item in failures])
        states = {item["repository"]: item for item in self.store.inspect()}
        self.assertEqual("queued", states["mezz/Broken"]["requests"][0]["status"])
        self.assertEqual([], states["mezz/Healthy"]["requests"])


if __name__ == "__main__":
    unittest.main()
