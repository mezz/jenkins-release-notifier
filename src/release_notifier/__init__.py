"""Jenkins release and build notification core."""

from .model import CommentTarget, DiscordNotification, ReleaseLink, ReleaseRequest

__all__ = ["CommentTarget", "DiscordNotification", "ReleaseLink", "ReleaseRequest"]
__version__ = "0.3.1"
