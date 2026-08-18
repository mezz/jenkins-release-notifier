# Jenkins Release Notifier

Posts a comment when a pull request or issue is included in a published
release.

Projects send the notifier a version, a commit range, and release links. The
notifier finds the pull requests and fixed issues in that range and comments on
them using the GitHub credential configured on the worker job.

## Documentation

- [Setup](docs/setup.md)
- [Troubleshooting](docs/operations.md)
- [Changelog](CHANGELOG.md)

## Development

The runtime uses Python 3.11 or newer and has no third-party dependencies.
