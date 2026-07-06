"""Ember error hierarchy.

A host app only needs to catch EmberError: every library error is a
subclass. Convenient for falling back to yt-dlp.
"""


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
    """

    def __init__(self, message: str, service: str = ""):
        super().__init__(message)
        self.service = service
