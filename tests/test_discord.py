from __future__ import annotations

import io
import json
import unittest
import urllib.error
from collections import deque
from typing import Any

from release_notifier.discord import DiscordClient
from release_notifier.errors import PermanentDiscordError, TransientDiscordError

from tests.support import discord_notification


class FakeResponse:
    def __init__(self, value: Any) -> None:
        self._body = json.dumps(value).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def http_error(
    status: int,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b'{"message":"failure"}',
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://discord.com/api/webhooks/123/token?wait=true",
        status,
        "failure",
        headers or {},
        io.BytesIO(body),
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


class DiscordClientTest(unittest.TestCase):
    def test_waits_for_confirmation_and_disables_mentions(self) -> None:
        opener = QueueOpener(FakeResponse({"id": "123456789"}))
        client = DiscordClient(
            "https://discord.com/api/webhooks/123/token",
            opener=opener,
        )

        message_id = client.create_message(discord_notification())

        self.assertEqual("123456789", message_id)
        request = opener.requests[0]
        self.assertEqual(
            "https://discord.com/api/webhooks/123/token?wait=true",
            request.full_url,
        )
        payload = json.loads(request.data)
        self.assertEqual({"parse": []}, payload["allowed_mentions"])
        self.assertEqual("mezz/Example/main #42", payload["embeds"][0]["title"])

    def test_rate_limit_retries_using_server_delay(self) -> None:
        opener = QueueOpener(
            http_error(429, body=b'{"retry_after":7.5}'),
            FakeResponse({"id": "123456789"}),
        )
        delays: list[float] = []
        client = DiscordClient(
            "https://discord.com/api/webhooks/123/token",
            opener=opener,
            sleeper=delays.append,
            retry_delays=(1.0,),
        )

        client.create_message(discord_notification())

        self.assertEqual([7.5], delays)
        self.assertEqual(2, len(opener.requests))

    def test_unconfirmed_response_remains_retryable(self) -> None:
        opener = QueueOpener(FakeResponse({}))
        client = DiscordClient(
            "https://discord.com/api/webhooks/123/token",
            opener=opener,
            retry_delays=(),
        )

        with self.assertRaises(TransientDiscordError):
            client.create_message(discord_notification())

    def test_permanent_rejection_is_classified_without_exposing_webhook(self) -> None:
        opener = QueueOpener(http_error(404))
        client = DiscordClient(
            "https://discord.com/api/webhooks/123/super-secret-token",
            opener=opener,
        )

        with self.assertRaises(PermanentDiscordError) as raised:
            client.create_message(discord_notification())

        self.assertNotIn("super-secret-token", str(raised.exception))

    def test_rejects_non_discord_webhook_hosts(self) -> None:
        with self.assertRaisesRegex(Exception, "valid Discord webhook URL"):
            DiscordClient("https://attacker.invalid/api/webhooks/123/token")


if __name__ == "__main__":
    unittest.main()
