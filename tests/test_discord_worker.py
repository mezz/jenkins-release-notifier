from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from release_notifier.discord_worker import process_discord
from release_notifier.errors import TransientDiscordError
from release_notifier.model import DiscordNotification
from release_notifier.store import StateStore

from tests.support import discord_notification


class FakeDiscord:
    def __init__(self) -> None:
        self.attempts: list[str] = []
        self.failure: Exception | None = None

    def create_message(self, notification: DiscordNotification) -> str:
        self.attempts.append(notification.request_key)
        if self.failure is not None:
            raise self.failure
        return str(len(self.attempts))


class DiscordWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temporary_directory.name) / "state.json"
        self.store = StateStore.initialize(self.state_file)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_failure_stays_queued_and_retries_in_order(self) -> None:
        first = discord_notification("Example #1")
        second = discord_notification("Example #2")
        self.store.enqueue_discord(first)
        self.store.enqueue_discord(second)
        discord = FakeDiscord()
        discord.failure = TransientDiscordError("temporary outage")

        completed, failure = process_discord(self.store, discord)

        self.assertEqual((), completed)
        self.assertEqual(first.request_key, failure.request_key)
        self.assertEqual(first, self.store.next_discord())
        self.assertEqual("temporary outage", self.store.inspect_discord()[0]["lastError"])

        discord.failure = None
        completed, failure = process_discord(self.store, discord)

        self.assertIsNone(failure)
        self.assertEqual(
            [first.request_key, second.request_key],
            [item.request_key for item in completed],
        )
        self.assertIsNone(self.store.next_discord())
        self.assertEqual(
            [first.request_key, first.request_key, second.request_key],
            discord.attempts,
        )


if __name__ == "__main__":
    unittest.main()
