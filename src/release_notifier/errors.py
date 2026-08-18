"""Exceptions raised by the release notifier."""


class NotifierError(RuntimeError):
    """Base class for expected notifier failures."""


class ValidationError(NotifierError):
    """A release request failed boundary validation."""


class UnsupportedSchemaError(ValidationError):
    """A request or saved state uses an unsupported schema version."""


class QueueConflictError(NotifierError):
    """A request conflicts with data already in the queue."""


class StoreError(NotifierError):
    """Queue state could not be read or updated."""


class GitHubError(NotifierError):
    """A GitHub operation failed."""


class TransientGitHubError(GitHubError):
    """A GitHub read request failed after its retry attempts."""


class PermanentGitHubError(GitHubError):
    """GitHub rejected an operation that should not be retried unchanged."""


class AmbiguousWriteError(GitHubError):
    """A comment may have been created even though its response was lost."""


class RangeError(NotifierError):
    """The requested release range is unavailable or invalid."""
