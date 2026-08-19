# Jenkins Release Notifier

Posts GitHub comments for published releases and optional Discord summaries for
Jenkins builds.

Projects send the notifier a version, a commit range, and release links. The
notifier finds the pull requests and fixed issues in that range and comments on
them using the GitHub credential configured on the worker job.

Projects can also send Discord build summaries from their Jenkins `post`
block. Discord messages use the same worker queue and retry schedule as GitHub
comments.

## Documentation

- [Setup](docs/setup.md)
- [Discord notifications](docs/discord.md)
- [Troubleshooting](docs/operations.md)
- [Changelog](CHANGELOG.md)

## Development

The runtime uses Python 3.11 or newer and has no third-party dependencies.
