"""Process queued release notifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .discovery import DiscoveryClient, discover_targets
from .model import CommentTarget, ReleaseRequest
from .store import StateStore


class DeliveryClient(DiscoveryClient, Protocol):
    def comment_bodies(self, repository: str, number: int) -> tuple[str, ...]: ...

    def create_comment(self, repository: str, number: int, body: str) -> None: ...


@dataclass(frozen=True)
class ProcessedRelease:
    request_key: str
    repository: str
    channel: str
    version: str
    targets: int


@dataclass(frozen=True)
class ProcessingFailure:
    repository: str
    channel: str
    request_key: str
    message: str


def marker_exists(
    client: DeliveryClient, request: ReleaseRequest, target: CommentTarget
) -> bool:
    return any(
        target.marker in body
        for body in client.comment_bodies(request.repository, target.number)
    )


def process_channel(
    store: StateStore,
    client: DeliveryClient,
    repository: str,
    channel: str,
) -> tuple[ProcessedRelease, ...]:
    """Process one channel in release order until it is empty or an operation fails."""

    completed: list[ProcessedRelease] = []
    while pending := store.next_request(repository, channel):
        request = pending.request
        try:
            targets = pending.targets
            if targets is None:
                targets = store.set_targets(request, discover_targets(client, request))

            for delivery in store.deliveries(request):
                if delivery.status != "pending":
                    continue
                target = delivery.target
                if marker_exists(client, request, target):
                    store.mark_delivery(request, target, "marker_confirmed")
                    continue
                client.create_comment(request.repository, target.number, target.body)
                store.mark_delivery(request, target, "created")

            store.complete(request)
            completed.append(
                ProcessedRelease(
                    request_key=request.request_key,
                    repository=request.repository,
                    channel=request.channel,
                    version=request.version,
                    targets=len(targets),
                )
            )
        except Exception as error:
            store.fail(request, str(error))
            raise
    return tuple(completed)


def process_all(
    store: StateStore, client: DeliveryClient
) -> tuple[tuple[ProcessedRelease, ...], tuple[ProcessingFailure, ...]]:
    """Process all channels, allowing an unrelated channel to continue after a failure."""

    completed: list[ProcessedRelease] = []
    failures: list[ProcessingFailure] = []
    for repository, channel in store.pending_channels():
        try:
            completed.extend(process_channel(store, client, repository, channel))
        except Exception as error:
            pending = store.next_request(repository, channel)
            failures.append(
                ProcessingFailure(
                    repository=repository,
                    channel=channel,
                    request_key=pending.request.request_key if pending else "unknown",
                    message=str(error),
                )
            )
    return tuple(completed), tuple(failures)
