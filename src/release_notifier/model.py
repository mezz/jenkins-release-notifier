"""Versioned release request and deterministic comment models."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from .errors import UnsupportedSchemaError, ValidationError


REQUEST_SCHEMA_VERSION = 1
DISCORD_NOTIFICATION_SCHEMA_VERSION = 1
COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
REPOSITORY_PART_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$")
SENSITIVE_URL_PARAMETER_NAMES = frozenset(
    {
        "accesskey",
        "accesskeyid",
        "accesstoken",
        "apikey",
        "authorization",
        "credential",
        "jwt",
        "password",
        "secret",
        "signature",
        "sig",
        "token",
    }
)
ALLOWED_FIELDS = frozenset(
    {
        "schemaVersion",
        "repository",
        "channel",
        "projectName",
        "version",
        "baseCommit",
        "headCommit",
        "message",
        "releaseLinks",
        "minecraftVersions",
        "modLoaders",
        "enhancementLabels",
    }
)
DISCORD_RESULTS = frozenset({"SUCCESS", "UNSTABLE", "FAILURE", "ABORTED", "NOT_BUILT"})


def _plain_string(value: Any, field: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{field} must not be empty")
    if len(normalized) > maximum:
        raise ValidationError(f"{field} must be at most {maximum} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValidationError(f"{field} must not contain control characters")
    return normalized


def _string_array(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _plain_string(item, f"{field}[{index}]", maximum=100)
        key = text.casefold()
        if key not in seen:
            result.append(text)
            seen.add(key)
    return tuple(result)


def _markdown_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValidationError(f"{field} must not be empty")
    if len(normalized) > 10_000:
        raise ValidationError(f"{field} must be at most 10000 characters")
    if any(
        (ord(character) < 32 and character not in {"\n", "\t"})
        or ord(character) == 127
        for character in normalized
    ):
        raise ValidationError(f"{field} must not contain control characters")
    return normalized


def _multiline_string(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValidationError(f"{field} must not be empty")
    if len(normalized) > maximum:
        raise ValidationError(f"{field} must be at most {maximum} characters")
    if any(
        (ord(character) < 32 and character not in {"\n", "\t"})
        or ord(character) == 127
        for character in normalized
    ):
        raise ValidationError(f"{field} must not contain control characters")
    return normalized


def _validated_url(value: Any, field: str) -> str:
    url = _plain_string(value, field, maximum=2048)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError(f"{field} must be an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError(f"{field} must not contain credentials")
    query_names = {
        re.sub(r"[^a-z0-9]", "", name.casefold())
        for name, _ in parse_qsl(parsed.query, keep_blank_values=True)
    }
    fragment_names = {
        re.sub(r"[^a-z0-9]", "", name.casefold())
        for name, _ in parse_qsl(parsed.fragment, keep_blank_values=True)
    }
    if (query_names | fragment_names).intersection(SENSITIVE_URL_PARAMETER_NAMES):
        raise ValidationError(f"{field} must not contain credential parameters")
    return url


@dataclass(frozen=True)
class ReleaseLink:
    label: str
    url: str

    @classmethod
    def from_value(cls, value: Any, field: str) -> ReleaseLink:
        if not isinstance(value, Mapping):
            raise ValidationError(f"{field} must be an object")
        unknown = set(value) - {"label", "url"}
        if unknown:
            raise ValidationError(f"{field} has unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            _plain_string(value.get("label"), f"{field}.label", maximum=100),
            _validated_url(value.get("url"), f"{field}.url"),
        )

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "url": self.url}


@dataclass(frozen=True)
class DiscordNotification:
    schema_version: int
    title: str
    description: str
    footer: str
    link: str
    result: str

    @classmethod
    def from_dict(cls, value: Any) -> DiscordNotification:
        expected_fields = {
            "schemaVersion",
            "title",
            "description",
            "footer",
            "link",
            "result",
        }
        if not isinstance(value, Mapping) or set(value) != expected_fields:
            raise ValidationError("Discord notification has invalid fields")
        schema_version = value.get("schemaVersion")
        if schema_version != DISCORD_NOTIFICATION_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"unsupported Discord notification schema version: {schema_version!r}; "
                f"expected {DISCORD_NOTIFICATION_SCHEMA_VERSION}"
            )
        title = _plain_string(value.get("title"), "title", maximum=256)
        description = _multiline_string(
            value.get("description"), "description", maximum=4096
        )
        footer = _plain_string(value.get("footer"), "footer", maximum=2048)
        link = _validated_url(value.get("link"), "link")
        result = _plain_string(value.get("result"), "result", maximum=32).upper()
        if result not in DISCORD_RESULTS:
            raise ValidationError(
                f"result must be one of: {', '.join(sorted(DISCORD_RESULTS))}"
            )
        if len(title) + len(description) + len(footer) > 6000:
            raise ValidationError("Discord embed text must be at most 6000 characters")
        return cls(schema_version, title, description, footer, link, result)

    @property
    def request_key(self) -> str:
        encoded = self.to_json().encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "title": self.title,
            "description": self.description,
            "footer": self.footer,
            "link": self.link,
            "result": self.result,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ReleaseRequest:
    schema_version: int
    repository: str
    channel: str
    project_name: str
    version: str
    base_commit: str
    head_commit: str
    release_links: tuple[ReleaseLink, ...] = ()
    message: str | None = None
    minecraft_versions: tuple[str, ...] = ()
    mod_loaders: tuple[str, ...] = ()
    enhancement_labels: tuple[str, ...] = ("enhancement",)

    @classmethod
    def from_dict(cls, value: Any) -> ReleaseRequest:
        if not isinstance(value, Mapping):
            raise ValidationError("release request must be a JSON object")
        unknown = set(value) - ALLOWED_FIELDS
        if unknown:
            raise ValidationError(
                f"release request has unknown fields: {', '.join(sorted(unknown))}"
            )

        schema_version = value.get("schemaVersion")
        if schema_version != REQUEST_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"unsupported release request schema version: {schema_version!r}; "
                f"expected {REQUEST_SCHEMA_VERSION}"
            )

        repository = _plain_string(value.get("repository"), "repository", maximum=202)
        repository_parts = repository.split("/")
        if (
            len(repository_parts) != 2
            or any(part in {".", ".."} for part in repository_parts)
            or not all(REPOSITORY_PART_PATTERN.fullmatch(part) for part in repository_parts)
        ):
            raise ValidationError("repository must have the form owner/name")

        base_commit = _plain_string(value.get("baseCommit"), "baseCommit", maximum=40).lower()
        head_commit = _plain_string(value.get("headCommit"), "headCommit", maximum=40).lower()
        if not COMMIT_PATTERN.fullmatch(base_commit):
            raise ValidationError("baseCommit must be a full 40-character hexadecimal commit ID")
        if not COMMIT_PATTERN.fullmatch(head_commit):
            raise ValidationError("headCommit must be a full 40-character hexadecimal commit ID")

        links_value = value.get("releaseLinks")
        if not isinstance(links_value, list):
            raise ValidationError("releaseLinks must be an array")
        links = tuple(
            ReleaseLink.from_value(item, f"releaseLinks[{index}]")
            for index, item in enumerate(links_value)
        )
        if len({(link.label.casefold(), link.url) for link in links}) != len(links):
            raise ValidationError("releaseLinks must not contain duplicates")

        message = _markdown_string(value.get("message"), "message") if "message" in value else None

        enhancement_labels = _string_array(value.get("enhancementLabels"), "enhancementLabels")
        if "enhancementLabels" not in value:
            enhancement_labels = ("enhancement",)

        return cls(
            schema_version=schema_version,
            repository=repository,
            channel=_plain_string(value.get("channel"), "channel", maximum=256),
            project_name=_plain_string(value.get("projectName"), "projectName", maximum=256),
            version=_plain_string(value.get("version"), "version", maximum=256),
            base_commit=base_commit,
            head_commit=head_commit,
            release_links=links,
            message=message,
            minecraft_versions=_string_array(value.get("minecraftVersions"), "minecraftVersions"),
            mod_loaders=_string_array(value.get("modLoaders"), "modLoaders"),
            enhancement_labels=enhancement_labels,
        )

    @classmethod
    def from_json(cls, text: str) -> ReleaseRequest:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValidationError(f"release request is not valid JSON: {error.msg}") from error
        return cls.from_dict(value)

    @property
    def repository_key(self) -> str:
        return self.repository.casefold()

    @property
    def request_key(self) -> str:
        identity = {
            "schemaVersion": self.schema_version,
            "repository": self.repository_key,
            "channel": self.channel,
            "version": self.version,
            "headCommit": self.head_commit,
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "repository": self.repository,
            "channel": self.channel,
            "projectName": self.project_name,
            "version": self.version,
            "baseCommit": self.base_commit,
            "headCommit": self.head_commit,
            "releaseLinks": [link.to_dict() for link in self.release_links],
        }
        if self.minecraft_versions:
            value["minecraftVersions"] = list(self.minecraft_versions)
        if self.message is not None:
            value["message"] = self.message
        if self.mod_loaders:
            value["modLoaders"] = list(self.mod_loaders)
        if self.enhancement_labels != ("enhancement",):
            value["enhancementLabels"] = list(self.enhancement_labels)
        return value

    def to_json(self, *, pretty: bool = False) -> str:
        if pretty:
            return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class CommentTarget:
    kind: str
    number: int
    visible_body: str
    marker: str

    def __post_init__(self) -> None:
        if self.kind not in {"pull_request", "issue"}:
            raise ValidationError(f"unsupported comment target kind: {self.kind}")
        if self.number < 1:
            raise ValidationError("comment target number must be positive")

    @property
    def body(self) -> str:
        return f"{self.visible_body}\n\n<!-- {self.marker} -->"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "number": self.number,
            "visibleBody": self.visible_body,
            "marker": self.marker,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CommentTarget:
        if not isinstance(value, Mapping) or set(value) != {
            "kind",
            "number",
            "visibleBody",
            "marker",
        }:
            raise ValidationError("stored comment target has invalid fields")
        number = value["number"]
        visible_body = value["visibleBody"]
        marker = value["marker"]
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValidationError("stored comment target number must be an integer")
        if not isinstance(visible_body, str) or len(visible_body) > 50_000:
            raise ValidationError("stored comment target body is invalid")
        if any(
            (ord(character) < 32 and character not in {"\n", "\r", "\t"})
            or ord(character) == 127
            for character in visible_body
        ):
            raise ValidationError("stored comment target body contains control characters")
        if not isinstance(marker, str) or len(marker) > 256:
            raise ValidationError("stored comment target marker is invalid")
        return cls(
            kind=value["kind"],
            number=number,
            visible_body=visible_body,
            marker=marker,
        )
