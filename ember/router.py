"""Точка входа: определяет сервис по URL и запускает нужный извлекатель."""

from __future__ import annotations

from typing import List, Optional

import requests

from .cookies import cookies_from_browser as _cookies_from_browser
from .errors import EmberError, UnsupportedUrlError
from .http import make_context
from .models import Playlist, Result
from .services import (bluesky, facebook, instagram, newgrounds, ok, pinterest,
                       reddit, rutube, soundcloud, tiktok, tumblr, twitch,
                       twitter, vimeo, vk)

_SERVICES = [
    tiktok, twitter, instagram, reddit,
    vimeo, soundcloud, pinterest, tumblr, bluesky, newgrounds,
    rutube, ok, vk, facebook, twitch,
]


def supported_services() -> List[str]:
    """Список поддерживаемых сервисов."""
    return [s.SERVICE for s in _SERVICES]


def _match_service(url: str):
    for service in _SERVICES:
        for pattern in service.PATTERNS:
            if pattern.match(url):
                return service
    return None


def can_extract(url: str) -> bool:
    """True, если ссылку стоит отдавать Ember (иначе — вашему yt-dlp)."""
    return _match_service(url.strip()) is not None


def extract(
    url: str,
    *,
    timeout: float = 15.0,
    proxies: Optional[dict] = None,
    cookies: Optional[dict] = None,
    cookies_from_browser: Optional[str] = None,
    browser_profile: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Result:
    """Извлекает прямые ссылки на медиа и метаданные по URL поста.

    Args:
        url: ссылка на пост (TikTok, Twitter/X, Instagram, Reddit).
        timeout: таймаут каждого HTTP-запроса, секунды.
        proxies: прокси в формате requests, например {"https": "http://..."}.
        cookies: cookies для сервиса вручную (dict {имя: значение}).
        cookies_from_browser: имя браузера ("chrome", "firefox", "edge",
            "brave", ...) — cookies возьмутся автоматически, как в
            yt-dlp --cookies-from-browser. Нужен установленный yt-dlp
            или browser_cookie3.
        browser_profile: профиль браузера для cookies_from_browser.
        session: своя requests.Session, если нужен полный контроль.

    Returns:
        Result со списком media (прямые URL + заголовки для скачивания).

    Raises:
        UnsupportedUrlError: сервис не поддерживается — используйте fallback.
        NetworkError: сетевая проблема.
        ExtractionError: пост недоступен или сервис изменил формат.
    """
    url = url.strip()
    service = _match_service(url)
    if service is None:
        raise UnsupportedUrlError(
            f"unsupported link: {url}. "
            f"Supported: {', '.join(supported_services())}")

    ctx = make_context(timeout=timeout, proxies=proxies, session=session)
    if cookies_from_browser:
        ctx.session.cookies.update(
            _cookies_from_browser(cookies_from_browser,
                                  service=service.SERVICE,
                                  profile=browser_profile))
    if cookies:
        ctx.session.cookies.update(cookies)
    return service.extract(ctx, url)


def supports_playlist(url: str) -> bool:
    """True, если для ссылки доступно извлечение плейлиста."""
    service = _match_service(url.strip())
    return service is not None and hasattr(service, "extract_playlist")


def extract_playlist(
    url: str,
    *,
    timeout: float = 15.0,
    proxies: Optional[dict] = None,
    cookies: Optional[dict] = None,
    cookies_from_browser: Optional[str] = None,
    browser_profile: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Playlist:
    """Извлекает плейлист/набор по ссылке (пока: SoundCloud sets).

    Для одиночной ссылки вернёт Playlist с одним элементом.
    Параметры — те же, что у extract().
    """
    url = url.strip()
    service = _match_service(url)
    if service is None:
        raise UnsupportedUrlError(f"unsupported link: {url}")
    if not hasattr(service, "extract_playlist"):
        raise EmberError(
            f"playlists are not supported for {service.SERVICE} yet — "
            "use extract() for a single link")

    ctx = make_context(timeout=timeout, proxies=proxies, session=session)
    if cookies_from_browser:
        ctx.session.cookies.update(
            _cookies_from_browser(cookies_from_browser,
                                  service=service.SERVICE, profile=browser_profile))
    if cookies:
        ctx.session.cookies.update(cookies)
    return service.extract_playlist(ctx, url)
