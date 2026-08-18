"""Release queue stored in a JSON file and Jenkins build descriptions."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import QueueConflictError, StoreError, UnsupportedSchemaError, ValidationError
from .model import CommentTarget, ReleaseRequest


STATE_SCHEMA_VERSION = 1
DESCRIPTION_PREFIX = "jenkins-release-notifier-state:v1:"
MAX_DESCRIPTION_LENGTH = 2_000_000
MAX_STATE_JSON_BYTES = 5_000_000
REQUEST_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DELIVERY_STATUSES = frozenset({"pending", "created", "marker_confirmed"})


@dataclass(frozen=True)
class PendingRequest:
    request: ReleaseRequest
    targets: tuple[CommentTarget, ...] | None


@dataclass(frozen=True)
class Delivery:
    target: CommentTarget
    status: str


@dataclass
class _RequestState:
    request: ReleaseRequest
    targets: tuple[CommentTarget, ...] | None
    delivery_statuses: list[str] | None
    last_error: str | None


@dataclass
class _ChannelState:
    repository: str
    channel: str
    checkpoint: str
    last_request_key: str | None
    requests: list[_RequestState]

    @property
    def repository_key(self) -> str:
        return self.repository.casefold()


def _expected_marker(request: ReleaseRequest, target: CommentTarget) -> str:
    kind = "pull-request" if target.kind == "pull_request" else "issue"
    return f"release-notifier:v1:{request.request_key}:{kind}:{target.number}"


class StateStore:
    """Mutable worker state backed by one JSON file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise StoreError(f"notifier state file does not exist: {self.path}; run 'init' first")
        try:
            if self.path.stat().st_size > MAX_STATE_JSON_BYTES:
                raise StoreError("notifier state file is too large")
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StoreError(f"could not read notifier state file: {self.path}") from error
        self._channels = self._parse_snapshot(value)

    @classmethod
    def initialize(cls, path: Path | str) -> StateStore:
        state_path = Path(path)
        if state_path.exists():
            return cls(state_path)
        cls._write_snapshot(
            state_path,
            {"schemaVersion": STATE_SCHEMA_VERSION, "channels": []},
        )
        return cls(state_path)

    @staticmethod
    def _write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n"
        if len(encoded.encode("utf-8")) > MAX_STATE_JSON_BYTES:
            raise StoreError("notifier state payload is too large")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
            try:
                temporary.write_text(encoded, encoding="utf-8")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as error:
            raise StoreError(f"could not write notifier state file: {path}") from error

    @classmethod
    def _parse_snapshot(cls, value: Any) -> list[_ChannelState]:
        if not isinstance(value, dict):
            raise StoreError("notifier state must be a JSON object")
        if set(value) != {"schemaVersion", "channels"}:
            raise StoreError("notifier state has invalid fields")
        version = value.get("schemaVersion")
        if version != STATE_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"unsupported notifier state schema version: {version!r}; "
                f"expected {STATE_SCHEMA_VERSION}"
            )
        channels_value = value.get("channels")
        if not isinstance(channels_value, list):
            raise StoreError("notifier state has no channel list")

        channels: list[_ChannelState] = []
        seen_channels: set[tuple[str, str]] = set()
        seen_requests: set[str] = set()
        try:
            for channel_index, channel_value in enumerate(channels_value):
                if not isinstance(channel_value, dict) or set(channel_value) != {
                    "repository",
                    "channel",
                    "checkpoint",
                    "lastRequestKey",
                    "requests",
                }:
                    raise StoreError(f"stored channel {channel_index} has invalid fields")
                probe = ReleaseRequest.from_dict(
                    {
                        "schemaVersion": 1,
                        "repository": channel_value["repository"],
                        "channel": channel_value["channel"],
                        "projectName": "state validation",
                        "version": "state validation",
                        "baseCommit": channel_value["checkpoint"],
                        "headCommit": channel_value["checkpoint"],
                        "releaseLinks": [],
                    }
                )
                channel_key = (probe.repository_key, probe.channel)
                if channel_key in seen_channels:
                    raise StoreError("stored notifier state contains a duplicate channel")
                seen_channels.add(channel_key)

                last_request_key = channel_value["lastRequestKey"]
                if last_request_key is not None and (
                    not isinstance(last_request_key, str)
                    or not REQUEST_KEY_PATTERN.fullmatch(last_request_key)
                ):
                    raise StoreError("stored channel has an invalid last request key")
                requests_value = channel_value["requests"]
                if not isinstance(requests_value, list):
                    raise StoreError("stored channel requests must be an array")

                requests: list[_RequestState] = []
                expected_base = probe.base_commit
                for request_index, request_value in enumerate(requests_value):
                    if not isinstance(request_value, dict) or set(request_value) != {
                        "request",
                        "targets",
                        "deliveryStatuses",
                        "lastError",
                    }:
                        raise StoreError(
                            f"stored request {request_index} in channel "
                            f"{probe.channel} has invalid fields"
                        )
                    request = ReleaseRequest.from_dict(request_value["request"])
                    if request.request_key in seen_requests:
                        raise StoreError("stored notifier state contains a duplicate request")
                    seen_requests.add(request.request_key)
                    if (
                        request.repository_key != probe.repository_key
                        or request.channel != probe.channel
                    ):
                        raise StoreError("stored request is in the wrong repository/channel")
                    if request.base_commit != expected_base:
                        raise QueueConflictError(
                            f"stored request chain expected base {expected_base}, "
                            f"got {request.base_commit}"
                        )
                    expected_base = request.head_commit

                    targets_value = request_value["targets"]
                    statuses_value = request_value["deliveryStatuses"]
                    if targets_value is None:
                        if statuses_value is not None:
                            raise StoreError("stored request without targets has delivery statuses")
                        targets = None
                        statuses = None
                    else:
                        if not isinstance(targets_value, list) or not isinstance(
                            statuses_value, list
                        ):
                            raise StoreError("stored targets and delivery statuses must be arrays")
                        targets = tuple(CommentTarget.from_dict(item) for item in targets_value)
                        statuses = list(statuses_value)
                        if len(targets) != len(statuses):
                            raise StoreError("stored targets and delivery status lengths differ")
                        seen_targets: set[tuple[str, int]] = set()
                        for target, status in zip(targets, statuses, strict=True):
                            target_key = (target.kind, target.number)
                            if target_key in seen_targets:
                                raise StoreError("stored request contains a duplicate target")
                            seen_targets.add(target_key)
                            if target.marker != _expected_marker(request, target):
                                raise StoreError("stored target has an invalid idempotency marker")
                            if status not in DELIVERY_STATUSES:
                                raise StoreError(f"invalid stored delivery status: {status!r}")
                    last_error = request_value["lastError"]
                    if last_error is not None and (
                        not isinstance(last_error, str)
                        or len(last_error) > 2000
                        or any(
                            ord(character) < 32 or ord(character) == 127
                            for character in last_error
                        )
                    ):
                        raise StoreError("stored request error is invalid")
                    requests.append(_RequestState(request, targets, statuses, last_error))

                channels.append(
                    _ChannelState(
                        repository=probe.repository,
                        channel=probe.channel,
                        checkpoint=probe.base_commit,
                        last_request_key=last_request_key,
                        requests=requests,
                    )
                )
        except (ValidationError, KeyError, TypeError, ValueError) as error:
            raise StoreError("stored notifier state failed validation") from error
        return channels

    def _snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": STATE_SCHEMA_VERSION,
            "channels": [
                {
                    "repository": channel.repository,
                    "channel": channel.channel,
                    "checkpoint": channel.checkpoint,
                    "lastRequestKey": channel.last_request_key,
                    "requests": [
                        {
                            "request": item.request.to_dict(),
                            "targets": (
                                [target.to_dict() for target in item.targets]
                                if item.targets is not None
                                else None
                            ),
                            "deliveryStatuses": item.delivery_statuses,
                            "lastError": item.last_error,
                        }
                        for item in channel.requests
                    ],
                }
                for channel in sorted(
                    self._channels, key=lambda item: (item.repository_key, item.channel)
                )
            ],
        }

    def _save(self) -> None:
        self._write_snapshot(self.path, self._snapshot())

    def _find_channel(self, repository: str, channel: str) -> _ChannelState | None:
        repository_key = repository.casefold()
        return next(
            (
                item
                for item in self._channels
                if item.repository_key == repository_key and item.channel == channel
            ),
            None,
        )

    @staticmethod
    def _find_request(channel: _ChannelState, request: ReleaseRequest) -> _RequestState:
        state = next(
            (item for item in channel.requests if item.request.request_key == request.request_key),
            None,
        )
        if state is None:
            raise StoreError(f"request is missing: {request.request_key}")
        return state

    def enqueue(self, request: ReleaseRequest) -> bool:
        """Queue a request; return False when it is already pending or just completed."""

        for channel in self._channels:
            for item in channel.requests:
                if item.request.request_key != request.request_key:
                    continue
                if item.request.to_json() != request.to_json():
                    raise QueueConflictError(
                        f"request key {request.request_key} already exists with "
                        "different release data"
                    )
                return False

        channel = self._find_channel(request.repository, request.channel)
        if channel is None:
            channel = _ChannelState(
                repository=request.repository,
                channel=request.channel,
                checkpoint=request.base_commit,
                last_request_key=None,
                requests=[],
            )
            self._channels.append(channel)
        if channel.last_request_key == request.request_key:
            return False
        expected_base = (
            channel.requests[-1].request.head_commit
            if channel.requests
            else channel.checkpoint
        )
        if expected_base != request.base_commit:
            raise QueueConflictError(
                f"release range does not continue channel {request.repository}/{request.channel}: "
                f"expected base {expected_base}, got {request.base_commit}"
            )
        channel.requests.append(_RequestState(request, None, None, None))
        self._save()
        return True

    def pending_channels(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (channel.repository, channel.channel)
            for channel in sorted(
                self._channels, key=lambda item: (item.repository_key, item.channel)
            )
            if channel.requests
        )

    def next_request(self, repository: str, channel: str) -> PendingRequest | None:
        state = self._find_channel(repository, channel)
        if state is None or not state.requests:
            return None
        item = state.requests[0]
        return PendingRequest(item.request, item.targets)

    def set_targets(
        self, request: ReleaseRequest, targets: Iterable[CommentTarget]
    ) -> tuple[CommentTarget, ...]:
        channel = self._find_channel(request.repository, request.channel)
        if channel is None:
            raise StoreError(f"request channel is missing: {request.repository}/{request.channel}")
        state = self._find_request(channel, request)
        if state.targets is not None:
            return state.targets
        target_tuple = tuple(targets)
        seen: set[tuple[str, int]] = set()
        for target in target_tuple:
            key = (target.kind, target.number)
            if key in seen:
                raise StoreError("target plan contains a duplicate target")
            seen.add(key)
            if target.marker != _expected_marker(request, target):
                raise StoreError("target plan has an invalid idempotency marker")
        state.targets = target_tuple
        state.delivery_statuses = ["pending"] * len(target_tuple)
        state.last_error = None
        self._save()
        return target_tuple

    def deliveries(self, request: ReleaseRequest) -> tuple[Delivery, ...]:
        channel = self._find_channel(request.repository, request.channel)
        if channel is None:
            raise StoreError(f"request channel is missing: {request.repository}/{request.channel}")
        state = self._find_request(channel, request)
        if state.targets is None or state.delivery_statuses is None:
            raise StoreError(f"request has no stored targets: {request.request_key}")
        return tuple(
            Delivery(target, status)
            for target, status in zip(state.targets, state.delivery_statuses, strict=True)
        )

    def mark_delivery(
        self, request: ReleaseRequest, target: CommentTarget, status: str
    ) -> None:
        if status not in DELIVERY_STATUSES - {"pending"}:
            raise StoreError(f"invalid successful delivery status: {status}")
        channel = self._find_channel(request.repository, request.channel)
        if channel is None:
            raise StoreError(f"request channel is missing: {request.repository}/{request.channel}")
        state = self._find_request(channel, request)
        if state.targets is None or state.delivery_statuses is None:
            raise StoreError(f"request has no stored targets: {request.request_key}")
        try:
            index = state.targets.index(target)
        except ValueError as error:
            raise StoreError(
                f"delivery target is missing from request {request.request_key}: "
                f"{target.kind} #{target.number}"
            ) from error
        state.delivery_statuses[index] = status
        self._save()

    def complete(self, request: ReleaseRequest) -> None:
        channel = self._find_channel(request.repository, request.channel)
        if channel is None or not channel.requests:
            raise StoreError(f"request channel is missing: {request.repository}/{request.channel}")
        state = self._find_request(channel, request)
        if channel.requests[0] is not state:
            raise StoreError("checkpoint advancement attempted out of queue order")
        if state.delivery_statuses is None:
            raise StoreError(f"request has no stored targets: {request.request_key}")
        pending = state.delivery_statuses.count("pending")
        if pending:
            raise StoreError(
                f"request {request.request_key} still has {pending} pending deliveries"
            )
        channel.checkpoint = request.head_commit
        channel.last_request_key = request.request_key
        channel.requests.pop(0)
        self._save()

    def fail(self, request: ReleaseRequest, message: str) -> None:
        channel = self._find_channel(request.repository, request.channel)
        if channel is None:
            return
        try:
            state = self._find_request(channel, request)
        except StoreError:
            return
        state.last_error = message.replace("\r", " ").replace("\n", " ")[:2000]
        self._save()

    def inspect(
        self, repository: str | None = None, channel: str | None = None
    ) -> list[dict[str, Any]]:
        repository_key = repository.casefold() if repository is not None else None
        result: list[dict[str, Any]] = []
        for item in sorted(
            self._channels, key=lambda value: (value.repository_key, value.channel)
        ):
            if repository_key is not None and item.repository_key != repository_key:
                continue
            if channel is not None and item.channel != channel:
                continue
            requests = []
            for request_state in item.requests:
                statuses = request_state.delivery_statuses or []
                requests.append(
                    {
                        "requestKey": request_state.request.request_key,
                        "version": request_state.request.version,
                        "baseCommit": request_state.request.base_commit,
                        "headCommit": request_state.request.head_commit,
                        "status": "queued" if request_state.targets is None else "delivering",
                        "targets": len(statuses),
                        "delivered": sum(status != "pending" for status in statuses),
                        "lastError": request_state.last_error,
                    }
                )
            result.append(
                {
                    "repository": item.repository,
                    "channel": item.channel,
                    "checkpoint": item.checkpoint,
                    "requests": requests,
                }
            )
        return result

    def describe(self) -> str:
        payload = json.dumps(
            self._snapshot(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(payload) > MAX_STATE_JSON_BYTES:
            raise StoreError("Jenkins notifier state payload is too large")
        encoded = base64.urlsafe_b64encode(zlib.compress(payload, level=9)).decode("ascii")
        description = f"{DESCRIPTION_PREFIX}{encoded}"
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise StoreError("Jenkins notifier state description is too large")
        return description

    @classmethod
    def _snapshot_from_description(cls, description: str) -> dict[str, Any]:
        if not description.startswith(DESCRIPTION_PREFIX):
            raise StoreError("Jenkins build description does not contain notifier state")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise StoreError("Jenkins notifier state description is too large")
        encoded = description.removeprefix(DESCRIPTION_PREFIX)
        try:
            compressed = base64.b64decode(encoded, altchars=b"-_", validate=True)
            decompressor = zlib.decompressobj()
            payload = decompressor.decompress(compressed, MAX_STATE_JSON_BYTES + 1)
            if (
                len(payload) > MAX_STATE_JSON_BYTES
                or decompressor.unconsumed_tail
                or decompressor.unused_data
                or not decompressor.eof
            ):
                raise StoreError("Jenkins notifier state payload is too large")
            value = json.loads(payload.decode("utf-8"))
        except (binascii.Error, zlib.error, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StoreError("Jenkins notifier state description is unreadable") from error
        cls._parse_snapshot(value)
        return value

    @classmethod
    def restore_description(cls, path: Path | str, description: str) -> StateStore:
        snapshot = cls._snapshot_from_description(description.strip())
        cls._write_snapshot(Path(path), snapshot)
        return cls(path)
