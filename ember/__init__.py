"""Ember — extract direct media links from social platforms.

An embeddable, cobalt-like (imputnet/cobalt) Python library.

Usage:
    import ember
    result = ember.extract("https://www.tiktok.com/@user/video/123...")
    for m in result.media:
        print(m.kind, m.url)

Logging: the package logs to the "ember" logger and is silent by default
(NullHandler). To see logs from your app:
    import logging; logging.basicConfig(); logging.getLogger("ember").setLevel(logging.INFO)
"""

import logging as _logging

# best practice: a library must not force output — silent by default
_logging.getLogger("ember").addHandler(_logging.NullHandler())

from .errors import (
    EmberError,
    ExtractionError,
    NetworkError,
    UnsupportedUrlError,
)
from .cookies import cookies_from_browser, cookies_from_file
from .download import (DownloadProgress, available_qualities, download,
                       download_media, ffmpeg_available, probe_size)
from .models import Media, MediaVariant, Playlist, Result, Subtitle
from .router import (can_extract, extract, extract_playlist, extract_timeline,
                     supported_services, supports_playlist, supports_timeline)

__version__ = "0.5.0"

__all__ = [
    "extract",
    "extract_playlist",
    "extract_timeline",
    "can_extract",
    "supports_playlist",
    "supports_timeline",
    "supported_services",
    "cookies_from_browser",
    "cookies_from_file",
    "download",
    "download_media",
    "available_qualities",
    "ffmpeg_available",
    "probe_size",
    "DownloadProgress",
    "MediaVariant",
    "Playlist",
    "Result",
    "Media",
    "Subtitle",
    "EmberError",
    "UnsupportedUrlError",
    "NetworkError",
    "ExtractionError",
    "__version__",
]
