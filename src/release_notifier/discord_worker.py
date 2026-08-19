"""Process queued Discord notifications in submission order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .model import DiscordNotification
from .store import StateStore


class DiscordDeliveryClient(Protocol):
    def create_message(self, notification: DiscordNotification) -> str: ...


@dataclass(frozen=True)
class ProcessedDiscordNotification:
    request_key: str
    title: str
    message_id: str


@dataclass(frozen=True)
class DiscordProcessingFailure:
    request_key: str
    title: str
    message: str


def process_discord(
    store: StateStore,
    client: DiscordDeliveryClient,
) -> tuple[tuple[ProcessedDiscordNotification, ...], DiscordProcessingFailure | None]:
    completed: list[ProcessedDiscordNotification] = []
    while notification := store.next_discord():
        try:
            message_id = client.create_message(notification)
            store.complete_discord(notification)
            completed.append(
                ProcessedDiscordNotification(
                    request_key=notification.request_key,
                    title=notification.title,
                    message_id=message_id,
                )
            )
        except Exception as error:
            store.fail_discord(notification, str(error))
            return (
                tuple(completed),
                DiscordProcessingFailure(
                    request_key=notification.request_key,
                    title=notification.title,
                    message=str(error),
                ),
            )
    return tuple(completed), None
