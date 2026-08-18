"""GitHub API client with retries for read requests."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any, Callable, Mapping

from .errors import (
    AmbiguousWriteError,
    GitHubError,
    PermanentGitHubError,
    TransientGitHubError,
    ValidationError,
)


API_VERSION = "2022-11-28"
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DEFAULT_RETRY_DELAYS = (2.0, 5.0, 15.0)


@dataclass(frozen=True)
class CompareCommit:
    sha: str
    message: str


@dataclass(frozen=True)
class ComparedRange:
    status: str
    commits: tuple[CompareCommit, ...]


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    merged_at: str


@dataclass(frozen=True)
class Issue:
    number: int
    labels: frozenset[str]
    is_pull_request: bool


def get_next_link(link_header: str) -> str | None:
    for section in link_header.split(","):
        if 'rel="next"' not in section:
            continue
        start = section.find("<")
        end = section.find(">", start + 1)
        if 0 <= start < end:
            return section[start + 1 : end]
    return None


class GitHubClient:
    """GitHub reads plus the single allowed mutation: issue comments."""

    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        *,
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
        timeout_seconds: float = 30.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        max_server_delay: float = 60.0,
    ) -> None:
        if not token.strip():
            raise ValidationError("GitHub token environment variable is empty")
        if timeout_seconds <= 0:
            raise ValidationError("GitHub request timeout must be positive")
        if max_server_delay < 0 or any(delay < 0 for delay in retry_delays):
            raise ValidationError("GitHub retry delays must not be negative")
        parsed = urllib.parse.urlsplit(api_url.rstrip("/"))
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValidationError("GitHub API URL must be an absolute URL without credentials")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValidationError("GitHub API URL must use HTTPS (except for a local test server)")
        self._token = token
        self.api_url = api_url.rstrip("/")
        self._api_origin = (parsed.scheme.casefold(), parsed.netloc.casefold())
        self.retry_delays = retry_delays
        self.timeout_seconds = timeout_seconds
        self._opener = opener
        self._sleeper = sleeper
        self._clock = clock
        self.max_server_delay = max_server_delay

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            parsed = urllib.parse.urlsplit(path_or_url)
            origin = (parsed.scheme.casefold(), parsed.netloc.casefold())
            if origin != self._api_origin:
                raise GitHubError("GitHub pagination attempted to leave the configured API origin")
            return path_or_url
        if not path_or_url.startswith("/"):
            raise GitHubError("GitHub API path must start with '/'")
        return f"{self.api_url}{path_or_url}"

    @staticmethod
    def _headers(headers: Message | Mapping[str, str]) -> dict[str, str]:
        return {str(key).casefold(): str(value) for key, value in headers.items()}

    def _retry_delay(self, headers: Mapping[str, str], fallback: float) -> float:
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), self.max_server_delay)
            except ValueError:
                pass
        reset = headers.get("x-ratelimit-reset")
        if reset:
            try:
                return min(max(float(reset) - self._clock(), 0.0), self.max_server_delay)
            except ValueError:
                pass
        return min(max(fallback, 0.0), self.max_server_delay)

    @staticmethod
    def _response_detail(body: bytes) -> str:
        try:
            value = json.loads(body.decode("utf-8", errors="replace"))
            message = value.get("message") if isinstance(value, dict) else None
            if isinstance(message, str) and message:
                return message[:500]
        except json.JSONDecodeError:
            pass
        return "no error detail returned"

    def request_json(
        self,
        method: str,
        path_or_url: str,
        payload: Mapping[str, Any] | None = None,
        *,
        safe_to_retry: bool,
        ambiguous_write: bool = False,
    ) -> tuple[Any, dict[str, str]]:
        url = self._url(path_or_url)
        data = (
            None
            if payload is None
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "jenkins-release-notifier",
            "X-GitHub-Api-Version": API_VERSION,
        }
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        # urllib copies normal headers when following redirects. Mark the token as
        # unredirected so an API redirect cannot forward it to another origin.
        request.add_unredirected_header("Authorization", f"Bearer {self._token}")

        for attempt in range(len(self.retry_delays) + 1):
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    body = response.read()
                    response_headers = self._headers(response.headers)
                if not body:
                    return None, response_headers
                try:
                    return json.loads(body.decode("utf-8")), response_headers
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise GitHubError(
                        f"GitHub API returned invalid JSON for {method} {url}"
                    ) from error
            except urllib.error.HTTPError as error:
                try:
                    body = error.read()
                    response_headers = self._headers(error.headers or {})
                finally:
                    error.close()
                detail = self._response_detail(body)
                rate_limited = error.code == 403 and (
                    response_headers.get("x-ratelimit-remaining") == "0"
                    or "retry-after" in response_headers
                )
                retryable = error.code in RETRYABLE_STATUS_CODES or rate_limited
                if safe_to_retry and retryable and attempt < len(self.retry_delays):
                    self._sleeper(self._retry_delay(response_headers, self.retry_delays[attempt]))
                    continue
                message = f"GitHub API {method} {url} failed with HTTP {error.code}: {detail}"
                if ambiguous_write and retryable:
                    raise AmbiguousWriteError(message) from error
                if retryable:
                    raise TransientGitHubError(message) from error
                raise PermanentGitHubError(message) from error
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as error:
                if safe_to_retry and attempt < len(self.retry_delays):
                    self._sleeper(min(self.retry_delays[attempt], self.max_server_delay))
                    continue
                message = f"GitHub API {method} {url} failed without a definitive response"
                if ambiguous_write:
                    raise AmbiguousWriteError(message) from error
                raise TransientGitHubError(message) from error

        raise AssertionError("unreachable retry loop")

    def _paginate_array(self, path: str) -> list[Any]:
        result: list[Any] = []
        next_url: str | None = path
        while next_url:
            value, headers = self.request_json("GET", next_url, safe_to_retry=True)
            if not isinstance(value, list):
                raise GitHubError(
                    f"GitHub API returned a non-array collection for {self._url(next_url)}"
                )
            result.extend(value)
            next_url = get_next_link(headers.get("link", ""))
        return result

    def compare_range(self, repository: str, base: str, head: str) -> ComparedRange:
        repository_path = urllib.parse.quote(repository, safe="/")
        base_path = urllib.parse.quote(base, safe="")
        head_path = urllib.parse.quote(head, safe="")
        next_url: str | None = (
            f"/repos/{repository_path}/compare/{base_path}...{head_path}?per_page=100"
        )
        status: str | None = None
        commits: list[CompareCommit] = []
        seen: set[str] = set()
        while next_url:
            value, headers = self.request_json("GET", next_url, safe_to_retry=True)
            if not isinstance(value, dict):
                raise GitHubError("GitHub compare API returned an invalid response")
            page_status = value.get("status")
            if not isinstance(page_status, str):
                raise GitHubError("GitHub compare API did not return a range status")
            status = status or page_status
            if page_status != status:
                raise GitHubError("GitHub compare API returned inconsistent page status")
            page_commits = value.get("commits")
            if not isinstance(page_commits, list):
                raise GitHubError("GitHub compare API did not return a commit collection")
            for item in page_commits:
                if not isinstance(item, dict):
                    raise GitHubError("GitHub compare API returned an invalid commit")
                sha = item.get("sha", "")
                commit = item.get("commit", {})
                message = commit.get("message", "") if isinstance(commit, dict) else ""
                if not isinstance(sha, str) or not sha:
                    raise GitHubError("GitHub compare API returned a commit without an ID")
                if sha not in seen:
                    commits.append(CompareCommit(sha=sha, message=str(message)))
                    seen.add(sha)
            next_url = get_next_link(headers.get("link", ""))
        if status is None:
            raise GitHubError("GitHub compare API returned no pages")
        return ComparedRange(status=status, commits=tuple(commits))

    def pull_requests_for_commit(self, repository: str, sha: str) -> tuple[PullRequest, ...]:
        repository_path = urllib.parse.quote(repository, safe="/")
        items = self._paginate_array(
            f"/repos/{repository_path}/commits/{sha}/pulls?per_page=100"
        )
        result: list[PullRequest] = []
        for item in items:
            if not isinstance(item, dict):
                raise GitHubError("GitHub commit API returned an invalid pull request")
            merged_at = item.get("merged_at")
            if merged_at:
                number = item.get("number")
                if isinstance(number, bool) or not isinstance(number, int):
                    raise GitHubError("GitHub commit API returned a pull request without a number")
                result.append(
                    PullRequest(
                        number=number,
                        title=str(item.get("title", "")),
                        merged_at=str(merged_at),
                    )
                )
        return tuple(result)

    def graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        value, _ = self.request_json(
            "POST",
            "/graphql",
            {"query": query, "variables": dict(variables)},
            safe_to_retry=True,
        )
        if not isinstance(value, dict):
            raise GitHubError("GitHub GraphQL returned an invalid response")
        errors = value.get("errors")
        if errors:
            detail = ""
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = errors[0].get("message")
                if isinstance(message, str):
                    detail = f": {message[:500]}"
            raise PermanentGitHubError(
                f"GitHub GraphQL rejected the closing-issue query{detail}"
            )
        data = value.get("data")
        if not isinstance(data, dict):
            raise GitHubError("GitHub GraphQL did not return data")
        return data

    def closing_issues_for_pull_request(self, repository: str, number: int) -> tuple[int, ...]:
        owner, name = repository.split("/", 1)
        query = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 100, after: $cursor) {
        nodes { number repository { nameWithOwner } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
        result: list[int] = []
        cursor: str | None = None
        while True:
            data = self.graphql(
                query,
                {"owner": owner, "name": name, "number": number, "cursor": cursor},
            )
            try:
                references = data["repository"]["pullRequest"]["closingIssuesReferences"]
                nodes = references["nodes"]
                page_info = references["pageInfo"]
            except (KeyError, TypeError) as error:
                raise GitHubError(
                    "GitHub GraphQL returned incomplete closing-issue data"
                ) from error
            if not isinstance(nodes, list) or not isinstance(page_info, dict):
                raise GitHubError("GitHub GraphQL returned invalid closing-issue data")
            for node in nodes:
                if not isinstance(node, dict):
                    raise GitHubError("GitHub GraphQL returned an invalid closing issue")
                node_repository_value = node.get("repository")
                node_number = node.get("number")
                if not isinstance(node_repository_value, dict) or (
                    isinstance(node_number, bool) or not isinstance(node_number, int)
                ):
                    raise GitHubError("GitHub GraphQL returned an invalid closing issue")
                node_repository = node_repository_value.get("nameWithOwner", "")
                if str(node_repository).casefold() == repository.casefold():
                    result.append(node_number)
            if not page_info.get("hasNextPage"):
                return tuple(result)
            cursor = page_info.get("endCursor")
            if not cursor:
                raise GitHubError("GitHub GraphQL pagination omitted its next cursor")

    def issue(self, repository: str, number: int) -> Issue:
        repository_path = urllib.parse.quote(repository, safe="/")
        value, _ = self.request_json(
            "GET", f"/repos/{repository_path}/issues/{number}", safe_to_retry=True
        )
        if not isinstance(value, dict):
            raise GitHubError("GitHub issue API returned an invalid response")
        labels: set[str] = set()
        labels_value = value.get("labels", [])
        if not isinstance(labels_value, list):
            raise GitHubError("GitHub issue API returned an invalid label collection")
        for label in labels_value:
            if not isinstance(label, (dict, str)):
                raise GitHubError("GitHub issue API returned an invalid label")
            name = label.get("name", "") if isinstance(label, dict) else label
            if name:
                labels.add(str(name))
        return Issue(
            number=number,
            labels=frozenset(labels),
            is_pull_request="pull_request" in value,
        )

    def comment_bodies(self, repository: str, number: int) -> tuple[str, ...]:
        repository_path = urllib.parse.quote(repository, safe="/")
        items = self._paginate_array(
            f"/repos/{repository_path}/issues/{number}/comments?per_page=100"
        )
        result: list[str] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("body"), str):
                raise GitHubError("GitHub issue API returned an invalid comment")
            result.append(item["body"])
        return tuple(result)

    def create_comment(self, repository: str, number: int, body: str) -> None:
        repository_path = urllib.parse.quote(repository, safe="/")
        self.request_json(
            "POST",
            f"/repos/{repository_path}/issues/{number}/comments",
            {"body": body},
            safe_to_retry=False,
            ambiguous_write=True,
        )
