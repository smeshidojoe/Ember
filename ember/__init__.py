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
from .models import Media, Result
from .router import can_extract, extract, supported_services

__version__ = "0.1.0"

__all__ = [
    "extract",
    "can_extract",
    "supported_services",
    "cookies_from_browser",
    "Result",
    "Media",
    "EmberError",
    "UnsupportedUrlError",
    "NetworkError",
    "ExtractionError",
    "__version__",
]
