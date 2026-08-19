from __future__ import annotations

from collections import defaultdict
from typing import Callable

from release_notifier.github import ComparedRange, CompareCommit, Issue, PullRequest
from release_notifier.model import DiscordNotification, ReleaseRequest


def discord_notification(
    title: str = "mezz/Example/main #42",
    *,
    result: str = "SUCCESS",
) -> DiscordNotification:
    return DiscordNotification.from_dict(
        {
            "schemaVersion": 1,
            "title": title,
            "description": "**Result:** SUCCESS\n**Build:** #42",
            "footer": "Example Jenkins",
            "link": "https://ci.example.invalid/job/42/",
            "result": result,
        }
    )


def request(
    version: str = "1.2.3",
    base: str = "a" * 40,
    head: str = "b" * 40,
    *,
    repository: str = "mezz/Example",
    channel: str = "main",
    links: bool = True,
    metadata: bool = False,
    message: str | None = None,
) -> ReleaseRequest:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "repository": repository,
        "channel": channel,
        "projectName": "Example",
        "version": version,
        "baseCommit": base,
        "headCommit": head,
        "releaseLinks": (
            [{"label": "Maven", "url": f"https://example.invalid/{version}"}]
            if links
            else []
        ),
    }
    if metadata:
        value["minecraftVersions"] = ["1.21.1"]
        value["modLoaders"] = ["Fabric", "NeoForge"]
    if message is not None:
        value["message"] = message
    return ReleaseRequest.from_dict(value)


class FakeGitHub:
    def __init__(self) -> None:
        self.ranges: dict[tuple[str, str], ComparedRange | Exception] = {}
        self.pulls: dict[str, tuple[PullRequest, ...]] = {}
        self.closing: dict[int, tuple[int, ...]] = {}
        self.issues: dict[int, Issue] = {}
        self.comments: dict[int, list[str]] = defaultdict(list)
        self.create_attempts: list[int] = []
        self.create_hook: Callable[[int, str], None] | None = None

    def add_release(
        self,
        release: ReleaseRequest,
        *,
        pull_number: int | None = None,
        fixed_issue: int | None = None,
    ) -> None:
        message = f"Fix #{fixed_issue}" if fixed_issue else "Release change"
        self.ranges[(release.base_commit, release.head_commit)] = ComparedRange(
            "ahead", (CompareCommit(release.head_commit, message),)
        )
        if pull_number is not None:
            self.pulls[release.head_commit] = (
                PullRequest(pull_number, f"Change {pull_number}", "2026-08-18T00:00:00Z"),
            )
            if fixed_issue is not None:
                self.closing[pull_number] = (fixed_issue,)
        if fixed_issue is not None:
            self.issues[fixed_issue] = Issue(fixed_issue, frozenset(), False)

    def compare_range(self, repository: str, base: str, head: str) -> ComparedRange:
        value = self.ranges[(base, head)]
        if isinstance(value, Exception):
            raise value
        return value

    def pull_requests_for_commit(self, repository: str, sha: str) -> tuple[PullRequest, ...]:
        return self.pulls.get(sha, ())

    def closing_issues_for_pull_request(self, repository: str, number: int) -> tuple[int, ...]:
        return self.closing.get(number, ())

    def issue(self, repository: str, number: int) -> Issue:
        return self.issues.get(number, Issue(number, frozenset(), False))

    def comment_bodies(self, repository: str, number: int) -> tuple[str, ...]:
        return tuple(self.comments[number])

    def create_comment(self, repository: str, number: int, body: str) -> None:
        self.create_attempts.append(number)
        if self.create_hook is not None:
            self.create_hook(number, body)
            return
        self.comments[number].append(body)
