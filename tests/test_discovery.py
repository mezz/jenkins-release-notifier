from __future__ import annotations

import unittest

from release_notifier.discovery import discover_targets, issue_numbers_from_text
from release_notifier.errors import RangeError
from release_notifier.github import ComparedRange, CompareCommit, Issue, PullRequest

from tests.support import FakeGitHub, request


class DiscoveryTest(unittest.TestCase):
    def test_discovers_deduplicated_prs_and_fixed_issues(self) -> None:
        release = request(metadata=True)
        client = FakeGitHub()
        client.ranges[(release.base_commit, release.head_commit)] = ComparedRange(
            "ahead",
            (
                CompareCommit("c" * 40, "Fix #9 and resolves #12"),
                CompareCommit(release.head_commit, "Follow-up fixes #9"),
            ),
        )
        pull = PullRequest(3, "A change", "2026-08-18T00:00:00Z")
        client.pulls["c" * 40] = (pull,)
        client.pulls[release.head_commit] = (pull,)
        client.closing[3] = (9, 10, 99)
        client.issues[9] = Issue(9, frozenset(), False)
        client.issues[10] = Issue(10, frozenset({"Enhancement"}), False)
        client.issues[12] = Issue(12, frozenset(), False)
        client.issues[99] = Issue(99, frozenset(), True)

        targets = discover_targets(client, release)

        self.assertEqual(
            [("pull_request", 3), ("issue", 9), ("issue", 10), ("issue", 12)],
            [(target.kind, target.number) for target in targets],
        )
        self.assertIn("via pull request #3", targets[1].visible_body)
        self.assertIn("Thanks for requesting this feature", targets[2].visible_body)
        self.assertIn("Minecraft `1.21.1`", targets[0].visible_body)
        self.assertIn("Fabric, NeoForge", targets[0].visible_body)
        self.assertIn("https://example.invalid/1.2.3", targets[0].visible_body)
        self.assertEqual(len({target.marker for target in targets}), len(targets))

    def test_plain_library_with_no_links_renders_a_short_comment(self) -> None:
        release = request(links=False)
        client = FakeGitHub()
        client.add_release(release, pull_number=4)

        target = discover_targets(client, release)[0]

        self.assertEqual("🚀 This pull request is included in Example version `1.2.3`.", target.visible_body)

    def test_custom_message_is_used_for_every_target_and_keeps_release_links(self) -> None:
        release = request(message="Version 1.2.3 is now available.")
        client = FakeGitHub()
        client.add_release(release, pull_number=4, fixed_issue=5)

        targets = discover_targets(client, release)

        self.assertEqual(2, len(targets))
        for target in targets:
            self.assertTrue(target.visible_body.startswith("Version 1.2.3 is now available."))
            self.assertIn("https://example.invalid/1.2.3", target.visible_body)

    def test_identical_empty_range_has_no_targets(self) -> None:
        release = request(base="a" * 40, head="a" * 40)
        client = FakeGitHub()
        client.ranges[(release.base_commit, release.head_commit)] = ComparedRange("identical", ())

        self.assertEqual((), discover_targets(client, release))

    def test_rejects_divergent_and_truncated_ranges(self) -> None:
        release = request()
        client = FakeGitHub()
        client.ranges[(release.base_commit, release.head_commit)] = ComparedRange("diverged", ())
        with self.assertRaisesRegex(RangeError, "not an ancestor"):
            discover_targets(client, release)

        client.ranges[(release.base_commit, release.head_commit)] = ComparedRange(
            "ahead", (CompareCommit("c" * 40, "change"),)
        )
        with self.assertRaisesRegex(RangeError, "did not reach"):
            discover_targets(client, release)

    def test_fixed_reference_grammar(self) -> None:
        self.assertEqual({1, 2, 3}, issue_numbers_from_text("Fix #1, closes #2\nRESOLVED #3"))
        self.assertEqual(set(), issue_numbers_from_text("See #1 and prefix#2"))


if __name__ == "__main__":
    unittest.main()
