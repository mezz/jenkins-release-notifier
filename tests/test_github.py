from __future__ import annotations

import io
import json
import unittest
import urllib.error
from collections import deque
from typing import Any

from release_notifier.errors import AmbiguousWriteError, PermanentGitHubError
from release_notifier.github import GitHubClient


class FakeResponse:
    def __init__(self, value: Any, headers: dict[str, str] | None = None) -> None:
        self._body = b"" if value is None else json.dumps(value).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def http_error(status: int, *, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.github.com/test",
        status,
        "failure",
        headers or {},
        io.BytesIO(b'{"message":"temporary failure"}'),
    )


class QueueOpener:
    def __init__(self, *responses: Any) -> None:
        self.responses = deque(responses)
        self.requests: list[Any] = []

    def __call__(self, request: Any, *, timeout: float) -> Any:
        self.requests.append(request)
        value = self.responses.popleft()
        if isinstance(value, BaseException):
            raise value
        return value


class GitHubClientTest(unittest.TestCase):
    def test_safe_read_retries_and_respects_retry_after(self) -> None:
        opener = QueueOpener(
            http_error(503, headers={"Retry-After": "7"}),
            FakeResponse({"number": 2, "labels": []}),
        )
        delays: list[float] = []
        client = GitHubClient(
            "super-secret-token",
            opener=opener,
            sleeper=delays.append,
            retry_delays=(1.0,),
        )

        issue = client.issue("mezz/Example", 2)

        self.assertEqual(2, issue.number)
        self.assertEqual([7.0], delays)
        self.assertEqual(2, len(opener.requests))
        self.assertEqual("Bearer super-secret-token", opener.requests[0].get_header("Authorization"))
        self.assertNotIn("Authorization", opener.requests[0].headers)
        self.assertIn("Authorization", opener.requests[0].unredirected_hdrs)

    def test_rate_limited_403_uses_reset_guidance(self) -> None:
        opener = QueueOpener(
            http_error(
                403,
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1012"},
            ),
            FakeResponse({"number": 2, "labels": []}),
        )
        delays: list[float] = []
        client = GitHubClient(
            "token",
            opener=opener,
            sleeper=delays.append,
            clock=lambda: 1000.0,
            retry_delays=(1.0,),
        )

        client.issue("mezz/Example", 2)

        self.assertEqual([12.0], delays)

    def test_comment_write_is_never_blindly_retried(self) -> None:
        opener = QueueOpener(http_error(503))
        client = GitHubClient("token", opener=opener, sleeper=lambda _: None, retry_delays=(0, 0))

        with self.assertRaises(AmbiguousWriteError):
            client.create_comment("mezz/Example", 4, "Released")

        self.assertEqual(1, len(opener.requests))

    def test_permanent_comment_rejection_is_classified(self) -> None:
        opener = QueueOpener(http_error(422))
        client = GitHubClient("token", opener=opener)

        with self.assertRaises(PermanentGitHubError):
            client.create_comment("mezz/Example", 4, "Released")

    def test_paginated_comments_follow_all_pages_on_same_api_origin(self) -> None:
        next_url = "https://api.github.com/repos/mezz/Example/issues/2/comments?per_page=100&page=2"
        opener = QueueOpener(
            FakeResponse([{"body": "first"}], {"Link": f'<{next_url}>; rel="next"'}),
            FakeResponse([{"body": "marker"}]),
        )
        client = GitHubClient("token", opener=opener)

        self.assertEqual(("first", "marker"), client.comment_bodies("mezz/Example", 2))
        self.assertEqual(next_url, opener.requests[1].full_url)

    def test_pagination_cannot_send_token_to_another_origin(self) -> None:
        opener = QueueOpener(
            FakeResponse(
                [{"body": "first"}],
                {"Link": '<https://attacker.invalid/steal>; rel="next"'},
            )
        )
        client = GitHubClient("token", opener=opener)

        with self.assertRaisesRegex(Exception, "leave the configured API origin"):
            client.comment_bodies("mezz/Example", 2)

        self.assertEqual(1, len(opener.requests))

    def test_compare_commit_collection_is_paginated_and_deduplicated(self) -> None:
        head = "c" * 40
        next_url = f"https://api.github.com/repos/mezz/Example/compare/{'a' * 40}...{head}?page=2"
        opener = QueueOpener(
            FakeResponse(
                {
                    "status": "ahead",
                    "commits": [{"sha": "b" * 40, "commit": {"message": "one"}}],
                },
                {"Link": f'<{next_url}>; rel="next"'},
            ),
            FakeResponse(
                {
                    "status": "ahead",
                    "commits": [
                        {"sha": "b" * 40, "commit": {"message": "duplicate"}},
                        {"sha": head, "commit": {"message": "two"}},
                    ],
                }
            ),
        )
        client = GitHubClient("token", opener=opener)

        compared = client.compare_range("mezz/Example", "a" * 40, head)

        self.assertEqual("ahead", compared.status)
        self.assertEqual(["b" * 40, head], [commit.sha for commit in compared.commits])

    def test_pull_request_collection_is_paginated_and_filters_open_prs(self) -> None:
        next_url = "https://api.github.com/repos/mezz/Example/commits/abc/pulls?per_page=100&page=2"
        opener = QueueOpener(
            FakeResponse(
                [
                    {"number": 1, "title": "merged", "merged_at": "2026-08-18T00:00:00Z"},
                    {"number": 2, "title": "open", "merged_at": None},
                ],
                {"Link": f'<{next_url}>; rel="next"'},
            ),
            FakeResponse(
                [{"number": 3, "title": "also merged", "merged_at": "2026-08-18T01:00:00Z"}]
            ),
        )
        client = GitHubClient("token", opener=opener)

        pulls = client.pull_requests_for_commit("mezz/Example", "abc")

        self.assertEqual([1, 3], [pull.number for pull in pulls])

    def test_enterprise_api_root_is_preserved(self) -> None:
        opener = QueueOpener(FakeResponse({"number": 2, "labels": []}))
        client = GitHubClient(
            "token", api_url="https://github.example/api/v3", opener=opener
        )

        client.issue("mezz/Example", 2)

        self.assertEqual(
            "https://github.example/api/v3/repos/mezz/Example/issues/2",
            opener.requests[0].full_url,
        )

    def test_graphql_closing_issues_paginates_and_ignores_other_repositories(self) -> None:
        class GraphQLClient(GitHubClient):
            def __init__(self) -> None:
                super().__init__("token")
                self.calls = 0

            def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
                self.calls += 1
                final = self.calls == 2
                nodes = [
                    {"number": self.calls, "repository": {"nameWithOwner": "mezz/Example"}},
                    {"number": 99, "repository": {"nameWithOwner": "other/Repo"}},
                ]
                return {
                    "repository": {
                        "pullRequest": {
                            "closingIssuesReferences": {
                                "nodes": nodes,
                                "pageInfo": {
                                    "hasNextPage": not final,
                                    "endCursor": None if final else "next",
                                },
                            }
                        }
                    }
                }

        client = GraphQLClient()

        self.assertEqual((1, 2), client.closing_issues_for_pull_request("mezz/Example", 3))
        self.assertEqual(2, client.calls)

    def test_error_text_does_not_expose_authorization_token(self) -> None:
        opener = QueueOpener(http_error(404))
        client = GitHubClient("do-not-log-this-token", opener=opener)

        with self.assertRaises(PermanentGitHubError) as context:
            client.issue("mezz/Example", 404)

        self.assertNotIn("do-not-log-this-token", str(context.exception))


if __name__ == "__main__":
    unittest.main()
