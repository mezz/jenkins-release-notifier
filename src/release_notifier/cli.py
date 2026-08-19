"""Operator and Jenkins worker command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from .discord import DiscordClient
from .discord_worker import process_discord
from .errors import NotifierError, ValidationError
from .github import GitHubClient
from .model import DiscordNotification, ReleaseRequest
from .store import StateStore
from .worker import process_all


def _state_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _client(args: argparse.Namespace) -> GitHubClient:
    token = os.environ.get(args.token_env, "")
    if not token:
        raise ValidationError(f"GitHub token environment variable is not set: {args.token_env}")
    return GitHubClient(token=token, api_url=args.api_url)


def _discord_client(args: argparse.Namespace) -> DiscordClient:
    webhook_url = os.environ.get(args.discord_webhook_env, "")
    if not webhook_url:
        raise ValidationError(
            "Discord webhook environment variable is not set: "
            f"{args.discord_webhook_env}"
        )
    return DiscordClient(webhook_url)


def _add_state_file(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-file", required=True, help="worker JSON state file")


def _path_for_args(args: argparse.Namespace) -> Path:
    return _state_path(args.state_file)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ValidationError(f"release request environment variable is not set: {name}")
    return value


def _environment_lines(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return value.splitlines() if value else []


def _request_from_environment() -> ReleaseRequest:
    labels = _environment_lines("RELEASE_LINK_LABELS")
    urls = _environment_lines("RELEASE_LINK_URLS")
    if len(labels) != len(urls):
        raise ValidationError(
            "RELEASE_LINK_LABELS and RELEASE_LINK_URLS must have the same line count"
        )
    value: dict[str, Any] = {
        "schemaVersion": 1,
        "repository": _required_environment("RELEASE_REPOSITORY"),
        "channel": _required_environment("RELEASE_CHANNEL"),
        "projectName": _required_environment("RELEASE_PROJECT_NAME"),
        "version": _required_environment("RELEASE_VERSION"),
        "baseCommit": _required_environment("RELEASE_BASE_COMMIT"),
        "headCommit": _required_environment("RELEASE_HEAD_COMMIT"),
        "releaseLinks": [
            {"label": label, "url": url} for label, url in zip(labels, urls, strict=True)
        ],
    }
    optional_arrays = {
        "minecraftVersions": "RELEASE_MINECRAFT_VERSIONS",
        "modLoaders": "RELEASE_MOD_LOADERS",
    }
    for field, environment_name in optional_arrays.items():
        items = _environment_lines(environment_name)
        if items:
            value[field] = items
    message = os.environ.get("RELEASE_MESSAGE", "")
    if message.strip():
        value["message"] = message
    if os.environ.get("RELEASE_ENHANCEMENT_LABELS_PRESENT") == "true":
        value["enhancementLabels"] = _environment_lines("RELEASE_ENHANCEMENT_LABELS")
    return ReleaseRequest.from_dict(value)


def _discord_notification_from_environment() -> DiscordNotification:
    return DiscordNotification.from_dict(
        {
            "schemaVersion": 1,
            "title": _required_environment("DISCORD_TITLE"),
            "description": _required_environment("DISCORD_DESCRIPTION"),
            "footer": _required_environment("DISCORD_FOOTER"),
            "link": _required_environment("DISCORD_LINK"),
            "result": _required_environment("DISCORD_RESULT"),
        }
    )


def _add_github(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--token-env",
        default="GITHUB_RELEASE_NOTIFIER_TOKEN",
        help="environment variable containing the GitHub token",
    )
    parser.add_argument("--api-url", default="https://api.github.com")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release-notifier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize or verify a worker state file")
    _add_state_file(init_parser)

    parameter_parser = subparsers.add_parser(
        "submit-parameters", help="validate and enqueue Jenkins parameters"
    )
    _add_state_file(parameter_parser)

    discord_parameter_parser = subparsers.add_parser(
        "submit-discord-parameters",
        help="validate and enqueue Discord Jenkins parameters",
    )
    _add_state_file(discord_parameter_parser)

    describe_parser = subparsers.add_parser(
        "describe-state", help="encode queue state for a Jenkins build description"
    )
    _add_state_file(describe_parser)

    restore_parser = subparsers.add_parser(
        "restore-state", help="restore a state file from a Jenkins build description"
    )
    _add_state_file(restore_parser)
    restore_parser.add_argument("--description-file", required=True)

    process_parser = subparsers.add_parser("process", help="post queued notifications")
    _add_state_file(process_parser)
    _add_github(process_parser)
    process_parser.add_argument(
        "--discord-webhook-env",
        default="DISCORD_WEBHOOK_URL",
        help="environment variable containing the Discord webhook URL",
    )
    process_parser.add_argument(
        "--delivery",
        choices=("all", "github", "discord"),
        default="all",
        help="notification service to process",
    )

    pending_parser = subparsers.add_parser(
        "has-pending", help="return success when a notification service has queued work"
    )
    _add_state_file(pending_parser)
    pending_parser.add_argument(
        "--delivery",
        choices=("github", "discord"),
        required=True,
        help="notification service to check",
    )

    inspect_parser = subparsers.add_parser("inspect", help="inspect queue and delivery state")
    _add_state_file(inspect_parser)
    inspect_parser.add_argument("--repository")
    inspect_parser.add_argument("--channel")
    inspect_parser.add_argument("--json", action="store_true")

    return parser


def _human_inspect(
    channels: list[dict[str, Any]], discord_notifications: list[dict[str, Any]]
) -> str:
    lines: list[str] = []
    for channel in channels:
        lines.append(
            f"{channel['repository']}/{channel['channel']}: "
            f"checkpoint={channel['checkpoint']}"
        )
        for request in channel["requests"]:
            progress = f"{request['delivered']}/{request['targets']} targets"
            lines.append(
                f"  {request['requestKey']} {request['version']} {request['status']} {progress}"
            )
            lines.append(f"    {request['baseCommit']}..{request['headCommit']}")
            if request["lastError"]:
                lines.append(f"    error: {request['lastError']}")
    if discord_notifications:
        lines.append("Discord:")
        for notification in discord_notifications:
            lines.append(
                f"  {notification['requestKey']} {notification['result']} "
                f"{notification['title']}"
            )
            if notification["lastError"]:
                lines.append(f"    error: {notification['lastError']}")
    if not lines:
        return "No queued notifications."
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    if args.command == "init":
        path = _path_for_args(args)
        StateStore.initialize(path)
        print(f"Initialized notifier state: {path}")
        return 0

    if args.command == "submit-parameters":
        request = _request_from_environment()
        store = StateStore(_path_for_args(args))
        created = store.enqueue(request)
        print(f"{'Queued' if created else 'Already queued'} request {request.request_key}")
        return 0

    if args.command == "submit-discord-parameters":
        notification = _discord_notification_from_environment()
        store = StateStore(_path_for_args(args))
        created = store.enqueue_discord(notification)
        print(
            f"{'Queued' if created else 'Already queued'} Discord notification "
            f"{notification.request_key}"
        )
        return 0

    if args.command == "describe-state":
        print(StateStore(_path_for_args(args)).describe())
        return 0

    if args.command == "restore-state":
        try:
            description = Path(args.description_file).read_text(encoding="utf-8")
        except OSError as error:
            raise ValidationError(
                f"could not read Jenkins state description: {args.description_file}"
            ) from error
        StateStore.restore_description(_path_for_args(args), description)
        print("Restored notifier state from Jenkins build description")
        return 0

    if args.command == "process":
        store = StateStore(_path_for_args(args))
        completed = ()
        failures = ()
        if args.delivery in ("all", "github") and store.pending_channels():
            completed, failures = process_all(store, _client(args))
        for item in completed:
            print(
                f"Completed {item.repository}/{item.channel} {item.version} "
                f"({item.targets} targets): {item.request_key}"
            )
        for failure in failures:
            print(
                f"Failed {failure.repository}/{failure.channel} {failure.request_key}: "
                f"{failure.message}",
                file=sys.stderr,
            )
        discord_completed = ()
        discord_failure = None
        if args.delivery in ("all", "discord") and store.next_discord() is not None:
            discord_completed, discord_failure = process_discord(
                store, _discord_client(args)
            )
        for item in discord_completed:
            print(
                f"Completed Discord notification {item.title} "
                f"(message {item.message_id}): {item.request_key}"
            )
        if discord_failure is not None:
            print(
                f"Failed Discord notification {discord_failure.title} "
                f"{discord_failure.request_key}: {discord_failure.message}",
                file=sys.stderr,
            )
        return 1 if failures or discord_failure is not None else 0

    store = StateStore(_path_for_args(args))
    if args.command == "has-pending":
        if args.delivery == "github":
            return 0 if store.pending_channels() else 1
        return 0 if store.next_discord() is not None else 1

    if args.command == "inspect":
        channels = store.inspect(args.repository, args.channel)
        discord_notifications = store.inspect_discord()
        if args.json:
            print(
                json.dumps(
                    {
                        "channels": channels,
                        "discordNotifications": discord_notifications,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(_human_inspect(channels, discord_notifications))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        return run(parser.parse_args(argv))
    except (NotifierError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
