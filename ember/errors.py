"""Ember error hierarchy.

A host app only needs to catch EmberError: every library error is a
subclass. Convenient for falling back to yt-dlp.

Errors also carry a machine-readable `reason` so an app can react without
parsing the English message (offer a login, show "unavailable", retry
later, hand the link to a fallback downloader, ...).
"""

from typing import Optional


class Reason:
    """Why extraction failed, in a form code can branch on.

    Plain string constants, so `err.reason == ember.Reason.NEEDS_AUTH`
    works and the value is also readable in logs and JSON.
    """

    NEEDS_AUTH = "needs_auth"        # cookies / login required (or the account lacks access)
    RESTRICTED = "restricted"        # age wall, members-only, paid tier
    DELETED = "deleted"              # removed, private, or never existed
    GEOBLOCKED = "geoblocked"        # blocked in this region
    RATE_LIMITED = "rate_limited"    # HTTP 429 / temporary throttling
    IP_BLOCKED = "ip_blocked"        # datacenter/VPN address refused
    NO_MEDIA = "no_media"            # page loaded, but holds no downloadable media
    LIVE = "live"                    # a live stream, not a finished recording
    FORMAT_CHANGED = "format_changed"  # the service changed its response shape
    UNKNOWN = "unknown"

    ALL = (NEEDS_AUTH, RESTRICTED, DELETED, GEOBLOCKED, RATE_LIMITED,
           IP_BLOCKED, NO_MEDIA, LIVE, FORMAT_CHANGED, UNKNOWN)


# Ключевые слова -> причина. Работает по НАШИМ собственным текстам ошибок
# (не по чужим ответам), поэтому это не разбор произвольной прозы, а запасной
# путь: где причина проставлена явно, она всегда важнее.
_HINTS = (
    (Reason.IP_BLOCKED, ("blocked anonymous access", "network policy",
                         "datacenter", "different ip")),
    (Reason.NEEDS_AUTH, ("cookies", "logged-in", "log in", "login required",
                         "account", "auth")),
    (Reason.RATE_LIMITED, ("http 429", "rate limit", "too many requests")),
    (Reason.LIVE, ("live stream", "live hls", "is live")),
    (Reason.RESTRICTED, ("restricted", "age wall", "members-only", "premium",
                         "geo-block", "paid")),
    (Reason.GEOBLOCKED, ("geo-blocked", "not available in your")),
    (Reason.DELETED, ("deleted", "removed", "private", "not found",
                      "no longer available", "unavailable")),
    (Reason.NO_MEDIA, ("no video", "no image", "no photo", "no audio",
                       "no media", "no mp4", "no streams", "no downloadable",
                       "has no segments", "no quality variants")),
    (Reason.FORMAT_CHANGED, ("unexpected", "could not parse", "no flashvars")),
)


def infer_reason(message: str) -> str:
    """Best-effort reason for a message that did not declare one."""
    low = (message or "").lower()
    for reason, needles in _HINTS:
        if any(n in low for n in needles):
            return reason
    return Reason.UNKNOWN


class EmberError(Exception):
    """Base Ember error."""


class UnsupportedUrlError(EmberError):
    """The URL does not match any supported service."""


class NetworkError(EmberError):
    """Network error: timeout, dropped connection, HTTP 5xx, etc."""


class ExtractionError(EmberError):
    """The service responded but media could not be extracted.

    Common causes: post deleted/private, the service changed its page
    format, or authentication (cookies) is required.

    reason — one of the `Reason` constants; defaults to `Reason.UNKNOWN`
    so existing code that raises without it keeps working.
    """

    def __init__(self, message: str, service: str = "",
                 reason: Optional[str] = None):
        super().__init__(message)
        self.service = service
        self.reason = reason or infer_reason(message)

    @property
    def needs_auth(self) -> bool:
        """Shortcut: would account cookies plausibly fix this?"""
        return self.reason in (Reason.NEEDS_AUTH, Reason.RESTRICTED)
