"""Ember — извлечение прямых ссылок на медиа из соцсетей.

Аналог cobalt (imputnet/cobalt) в виде встраиваемой Python-библиотеки.
Поддерживаемые сервисы: TikTok, Twitter/X, Instagram, Reddit.

Использование:
    import ember
    result = ember.extract("https://www.tiktok.com/@user/video/123...")
    for m in result.media:
        print(m.kind, m.url)
"""

from .errors import (
    EmberError,
    ExtractionError,
    NetworkError,
    UnsupportedUrlError,
)
from .cookies import cookies_from_browser
from .download import (DownloadProgress, available_qualities, download,
                       download_media, ffmpeg_available)
from .models import Media, MediaVariant, Playlist, Result
from .router import (can_extract, extract, extract_playlist, supported_services,
                     supports_playlist)

__version__ = "0.1.0"

__all__ = [
    "extract",
    "extract_playlist",
    "can_extract",
    "supports_playlist",
    "supported_services",
    "cookies_from_browser",
    "download",
    "download_media",
    "available_qualities",
    "ffmpeg_available",
    "DownloadProgress",
    "MediaVariant",
    "Playlist",
    "Result",
    "Media",
    "EmberError",
    "UnsupportedUrlError",
    "NetworkError",
    "ExtractionError",
    "__version__",
]
