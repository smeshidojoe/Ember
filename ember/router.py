"""Entry point: pick the service by URL and run the matching extractor."""

from __future__ import annotations

from typing import List, Optional

import requests

import logging

from .cookies import cookies_from_browser as _cookies_from_browser
from .errors import EmberError, UnsupportedUrlError
from .http import make_context
from .models import Playlist, Result

log = logging.getLogger(__name__)
from .services import (bluesky, facebook, instagram, newgrounds, ok, pinterest,
                       reddit, rutube, soundcloud, tiktok, tumblr, twitch,
                       twitter, vimeo, vk)

_SERVICES = [
    tiktok, twitter, instagram, reddit,
    vimeo, soundcloud, pinterest, tumblr, bluesky, newgrounds,
    rutube, ok, vk, facebook, twitch,
]


def supported_services() -> List[str]:
    """List of supported service names."""
    return [s.SERVICE for s in _SERVICES]


def _match_service(url: str):
    for service in _SERVICES:
        for pattern in service.PATTERNS:
            if pattern.match(url):
                return service
    return None


def can_extract(url: str) -> bool:
    """True if the URL should go to Ember (otherwise to your yt-dlp)."""
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
    """Extract direct media links and metadata from a post URL.

    Args:
        url: post link (TikTok, Twitter/X, Instagram, Reddit, ...).
        timeout: per-request timeout, seconds.
        proxies: requests-style proxies, e.g. {"https": "http://..."}.
        cookies: manual cookies for the service (dict {name: value}).
        cookies_from_browser: browser name ("chrome", "firefox", "edge",
            "brave", ...) — cookies are read automatically, like
            yt-dlp --cookies-from-browser. Uses the built-in reader,
            falling back to yt-dlp / browser_cookie3.
        browser_profile: browser profile for cookies_from_browser.
        session: your own requests.Session for full control.

    Returns:
        Result with a media list (direct URLs + download headers).

    Raises:
        UnsupportedUrlError: service not supported — use a fallback.
        NetworkError: network problem.
        ExtractionError: post unavailable or the service changed its format.
    """
    url = url.strip()
    service = _match_service(url)
    if service is None:
        raise UnsupportedUrlError(
            f"unsupported link: {url}. "
            f"Supported: {', '.join(supported_services())}")

    log.info("extract: %s -> service=%s", url, service.SERVICE)
    ctx = make_context(timeout=timeout, proxies=proxies, session=session)
    if cookies_from_browser:
        log.debug("loading cookies from browser %s", cookies_from_browser)
        ctx.session.cookies.update(
            _cookies_from_browser(cookies_from_browser,
                                  service=service.SERVICE,
                                  profile=browser_profile))
    if cookies:
        ctx.session.cookies.update(cookies)
    return service.extract(ctx, url)


def supports_playlist(url: str) -> bool:
    """True if playlist extraction is available for the URL."""
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
    """Extract a playlist/set from a URL (currently: SoundCloud sets).

    For a single link returns a Playlist with one entry.
    Same parameters as extract().
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
