"""Discord webhook delivery with confirmation and bounded retries."""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from typing import Any, Callable, Mapping

from .errors import PermanentDiscordError, TransientDiscordError, ValidationError
from .model import DiscordNotification


DEFAULT_RETRY_DELAYS = (2.0, 5.0, 15.0)
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DISCORD_WEBHOOK_HOSTS = frozenset(
    {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}
)
WEBHOOK_PATH_PATTERN = re.compile(
    r"^/api(?:/v[0-9]+)?/webhooks/[0-9]+/[A-Za-z0-9._-]+/?$"
)
RESULT_COLORS = {
    "SUCCESS": 0x2ECC71,
    "UNSTABLE": 0xF1C40F,
    "FAILURE": 0xE74C3C,
    "ABORTED": 0x95A5A6,
    "NOT_BUILT": 0x95A5A6,
}


class DiscordClient:
    def __init__(
        self,
        webhook_url: str,
        *,
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
        timeout_seconds: float = 30.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        max_server_delay: float = 60.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValidationError("Discord request timeout must be positive")
        if max_server_delay < 0 or any(delay < 0 for delay in retry_delays):
            raise ValidationError("Discord retry delays must not be negative")
        parsed = urllib.parse.urlsplit(webhook_url.strip())
        if (
            parsed.scheme != "https"
            or parsed.hostname not in DISCORD_WEBHOOK_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not WEBHOOK_PATH_PATTERN.fullmatch(parsed.path)
        ):
            raise ValidationError("Discord webhook credential is not a valid Discord webhook URL")
        self._webhook_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "wait=true", "")
        )
        self.retry_delays = retry_delays
        self.timeout_seconds = timeout_seconds
        self._opener = opener
        self._sleeper = sleeper
        self.max_server_delay = max_server_delay

    @staticmethod
    def _headers(headers: Message | Mapping[str, str]) -> dict[str, str]:
        return {str(key).casefold(): str(value) for key, value in headers.items()}

    def _retry_delay(
        self,
        headers: Mapping[str, str],
        body: bytes,
        fallback: float,
    ) -> float:
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), self.max_server_delay)
            except ValueError:
                pass
        try:
            value = json.loads(body.decode("utf-8", errors="replace"))
            body_delay = value.get("retry_after") if isinstance(value, dict) else None
            if isinstance(body_delay, (int, float)) and not isinstance(body_delay, bool):
                return min(max(float(body_delay), 0.0), self.max_server_delay)
        except json.JSONDecodeError:
            pass
        return min(max(fallback, 0.0), self.max_server_delay)

    @staticmethod
    def _payload(notification: DiscordNotification) -> dict[str, Any]:
        return {
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "color": RESULT_COLORS[notification.result],
                    "description": notification.description,
                    "footer": {"text": notification.footer},
                    "title": notification.title,
                    "url": notification.link,
                }
            ],
        }

    def create_message(self, notification: DiscordNotification) -> str:
        data = json.dumps(self._payload(notification), separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self._webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": (
                    "DiscordBot (https://github.com/mezz/jenkins-release-notifier, 0.2.0)"
                ),
            },
            method="POST",
        )

        for attempt in range(len(self.retry_delays) + 1):
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    body = response.read()
                try:
                    value = json.loads(body.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise TransientDiscordError(
                        "Discord did not return a confirmed webhook message"
                    ) from error
                message_id = value.get("id") if isinstance(value, dict) else None
                if not isinstance(message_id, str) or not message_id.isdigit():
                    raise TransientDiscordError(
                        "Discord did not return a confirmed webhook message ID"
                    )
                return message_id
            except urllib.error.HTTPError as error:
                try:
                    body = error.read()
                    response_headers = self._headers(error.headers or {})
                finally:
                    error.close()
                if error.code in RETRYABLE_STATUS_CODES:
                    if attempt < len(self.retry_delays):
                        delay = self._retry_delay(
                            response_headers, body, self.retry_delays[attempt]
                        )
                        self._sleeper(delay)
                        continue
                    raise TransientDiscordError(
                        f"Discord webhook failed with HTTP {error.code}"
                    ) from error
                raise PermanentDiscordError(
                    f"Discord webhook was rejected with HTTP {error.code}"
                ) from error
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as error:
                if attempt < len(self.retry_delays):
                    self._sleeper(min(self.retry_delays[attempt], self.max_server_delay))
                    continue
                raise TransientDiscordError(
                    "Discord webhook failed without a definitive response"
                ) from error

        raise AssertionError("unreachable retry loop")
