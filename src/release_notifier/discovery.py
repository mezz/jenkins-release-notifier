"""Release-range discovery and deterministic comment rendering."""

from __future__ import annotations

import re
from typing import Protocol

from .errors import RangeError
from .github import ComparedRange, Issue, PullRequest
from .model import CommentTarget, ReleaseRequest


FIX_REFERENCE = re.compile(
    r"(?im)\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+#(?P<number>\d+)\b"
)


class DiscoveryClient(Protocol):
    def compare_range(self, repository: str, base: str, head: str) -> ComparedRange: ...

    def pull_requests_for_commit(self, repository: str, sha: str) -> tuple[PullRequest, ...]: ...

    def closing_issues_for_pull_request(self, repository: str, number: int) -> tuple[int, ...]: ...

    def issue(self, repository: str, number: int) -> Issue: ...


def issue_numbers_from_text(text: str) -> set[int]:
    return {int(match.group("number")) for match in FIX_REFERENCE.finditer(text or "")}


def _escape_markdown_label(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _release_description(request: ReleaseRequest) -> str:
    result = f"{request.project_name} version `{request.version}`"
    if request.minecraft_versions:
        versions = ", ".join(f"`{version}`" for version in request.minecraft_versions)
        result += f" for Minecraft {versions}"
    if request.mod_loaders:
        result += f" ({', '.join(request.mod_loaders)})"
    return result


def _with_links(summary: str, request: ReleaseRequest) -> str:
    if not request.release_links:
        return summary
    links = "\n".join(
        f"- [{_escape_markdown_label(link.label)}](<{link.url}>)"
        for link in request.release_links
    )
    return f"{summary}\n\n{links}"


def _marker(request: ReleaseRequest, kind: str, number: int) -> str:
    return f"release-notifier:v1:{request.request_key}:{kind}:{number}"


def _validate_range(request: ReleaseRequest, compared: ComparedRange) -> None:
    if compared.status not in {"ahead", "identical"}:
        raise RangeError(
            f"release base {request.base_commit} is not an ancestor of head {request.head_commit} "
            f"(GitHub compare status: {compared.status})"
        )
    if compared.status == "identical" and request.base_commit != request.head_commit:
        raise RangeError("GitHub reported an identical range for two different commit IDs")
    if compared.status == "ahead":
        if not compared.commits:
            raise RangeError("GitHub returned an empty commit list for an ahead release range")
        if compared.commits[-1].sha.casefold() != request.head_commit:
            raise RangeError("GitHub compare results did not reach the requested release head")


def discover_targets(client: DiscoveryClient, request: ReleaseRequest) -> tuple[CommentTarget, ...]:
    """Discover comment targets and render their messages for one release."""

    compared = client.compare_range(request.repository, request.base_commit, request.head_commit)
    _validate_range(request, compared)

    pull_requests: dict[int, PullRequest] = {}
    issue_to_pull_request: dict[int, int | None] = {}
    for commit in compared.commits:
        for issue_number in issue_numbers_from_text(commit.message):
            issue_to_pull_request.setdefault(issue_number, None)
        for pull_request in client.pull_requests_for_commit(request.repository, commit.sha):
            pull_requests.setdefault(pull_request.number, pull_request)

    for pull_request_number in sorted(pull_requests):
        for issue_number in client.closing_issues_for_pull_request(
            request.repository, pull_request_number
        ):
            if issue_to_pull_request.get(issue_number) is None:
                issue_to_pull_request[issue_number] = pull_request_number

    issues: dict[int, Issue] = {}
    for issue_number in sorted(issue_to_pull_request):
        issue = client.issue(request.repository, issue_number)
        if issue.is_pull_request:
            continue
        issues[issue_number] = issue

    release = _release_description(request)
    targets: list[CommentTarget] = []
    for number in sorted(pull_requests):
        summary = request.message or f"🚀 This pull request is included in {release}."
        targets.append(
            CommentTarget(
                kind="pull_request",
                number=number,
                visible_body=_with_links(summary, request),
                marker=_marker(request, "pull-request", number),
            )
        )

    enhancement_labels = {label.casefold() for label in request.enhancement_labels}
    for number, issue in sorted(issues.items()):
        labels = {label.casefold() for label in issue.labels}
        thanks = (
            "Thanks for requesting this feature!"
            if labels.intersection(enhancement_labels)
            else "Thanks for reporting this issue!"
        )
        pull_request_number = issue_to_pull_request[number]
        if request.message is not None:
            summary = request.message
        elif pull_request_number is None:
            summary = f"🚀 A fix for this issue is available in {release}.\n{thanks}"
        else:
            summary = (
                f"🚀 A fix for this issue is available in {release}, "
                f"via pull request #{pull_request_number}.\n{thanks}"
            )
        targets.append(
            CommentTarget(
                kind="issue",
                number=number,
                visible_body=_with_links(summary, request),
                marker=_marker(request, "issue", number),
            )
        )
    return tuple(targets)
