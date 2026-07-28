"""Entry point: pick the service by URL and run the matching extractor."""

from __future__ import annotations

import logging
from typing import List, Optional

import requests

import os

from .cookies import cookies_from_browser as _cookies_from_browser
from .cookies import cookies_from_file as _cookies_from_file
from .errors import EmberError, UnsupportedUrlError
from .http import make_context
from .models import Playlist, Result
from .services import (bluesky, facebook, imgur, instagram, newgrounds, ok,
                       pinterest, pornhub, redgifs, reddit, rutube, soundcloud,
                       tiktok, tumblr, twitch, twitter, vimeo, vk, xvideos)

log = logging.getLogger(__name__)

_SERVICES = [
    tiktok, twitter, instagram, reddit,
    vimeo, soundcloud, pinterest, tumblr, bluesky, newgrounds,
    rutube, ok, vk, facebook, twitch, pornhub, xvideos, redgifs, imgur,
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


def _match_profile(url: str):
    for service in _SERVICES:
        for pattern in getattr(service, "PROFILE_PATTERNS", ()):
            if pattern.match(url):
                return service
    return None


def _match_playlist(url: str):
    for service in _SERVICES:
        for pattern in getattr(service, "PLAYLIST_PATTERNS", ()):
            if pattern.match(url):
                return service
    return None


def _match_highlights(url: str):
    for service in _SERVICES:
        for pattern in getattr(service, "HIGHLIGHTS_PATTERNS", ()):
            if pattern.match(url):
                return service
    return None


def _build_ctx(timeout, proxies, session, cookies, cookies_from_browser,
               browser_profile, service_name):
    ctx = make_context(timeout=timeout, proxies=proxies, session=session)
    if cookies_from_browser:
        ctx.session.cookies.update(
            _cookies_from_browser(cookies_from_browser,
                                  service=service_name, profile=browser_profile))
    if cookies:
        if isinstance(cookies, (str, os.PathLike)):     # путь к cookies.txt
            cookies = _cookies_from_file(cookies)
        ctx.session.cookies.update(cookies)
    return ctx


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
    ctx = _build_ctx(timeout, proxies, session, cookies, cookies_from_browser,
                     browser_profile, service.SERVICE)
    return service.extract(ctx, url)


def extract_many(
    urls,
    *,
    workers: int = 6,
    timeout: float = 15.0,
    proxies: Optional[dict] = None,
    cookies: Optional[dict] = None,
    cookies_from_browser: Optional[str] = None,
    browser_profile: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> List[tuple]:
    """Extract many links at once, in parallel.

    Returns `[(url, result, error), ...]` in the SAME order as the input: on
    success `error` is None, on failure `result` is None and `error` holds the
    EmberError. Nothing is raised for a single bad link — a long batch should
    not die because one post was deleted.

    Args:
        urls: links to extract (any iterable).
        workers: parallel requests. Keep it modest: services rate-limit, and
            all of them share one cookie jar per call.
        everything else: as in extract().

    ```python
    for url, res, err in ember.extract_many(links):
        if err:
            print("skip", url, err.reason)
        else:
            ember.download(res, "downloads/")
    ```
    """
    from concurrent.futures import ThreadPoolExecutor

    urls = [u.strip() for u in urls]
    if not urls:
        return []

    def one(url: str):
        try:
            return (url, extract(
                url, timeout=timeout, proxies=proxies, cookies=cookies,
                cookies_from_browser=cookies_from_browser,
                browser_profile=browser_profile, session=session), None)
        except EmberError as e:
            log.debug("extract_many: %s failed: %s", url, e)
            return (url, None, e)

    # порядок входа сохраняем: executor.map отдаёт результаты по порядку
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls)))) as pool:
        return list(pool.map(one, urls))


def supports_playlist(url: str) -> bool:
    """True only if the URL really is a playlist/set (not a single post).

    A single track/video returns False — extract_playlist() still accepts it
    and yields a one-entry Playlist, but this predicate stays honest so a
    caller can decide whether to show a playlist UI.
    """
    return _match_playlist(url.strip()) is not None


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

    ctx = _build_ctx(timeout, proxies, session, cookies, cookies_from_browser,
                     browser_profile, service.SERVICE)
    return service.extract_playlist(ctx, url)


def supports_timeline(url: str) -> bool:
    """True if author-timeline extraction is available for the URL."""
    service = _match_profile(url.strip())
    return service is not None and hasattr(service, "extract_timeline")


def supports_highlights(url: str) -> bool:
    """True if story-highlight extraction is available for the URL."""
    service = _match_highlights(url.strip())
    return service is not None and hasattr(service, "extract_highlights")


def extract_highlights(
    url: str,
    *,
    limit: int = 30,
    timeout: float = 15.0,
    proxies: Optional[dict] = None,
    cookies: Optional[dict] = None,
    cookies_from_browser: Optional[str] = None,
    browser_profile: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Playlist:
    """List a profile's story highlights (the pinned covers above the posts).

    Returns a Playlist with one entry per highlight collection; each entry is
    a gallery of that collection's stories. Currently: Instagram (needs
    account cookies). Same auth params as extract().
    """
    url = url.strip()
    service = _match_highlights(url)
    if service is None or not hasattr(service, "extract_highlights"):
        raise UnsupportedUrlError(
            f"highlights are not supported for this URL: {url}")
    ctx = _build_ctx(timeout, proxies, session, cookies, cookies_from_browser,
                     browser_profile, service.SERVICE)
    return service.extract_highlights(ctx, url, limit)


def extract_timeline(
    url: str,
    *,
    limit: int = 30,
    timeout: float = 15.0,
    proxies: Optional[dict] = None,
    cookies: Optional[dict] = None,
    cookies_from_browser: Optional[str] = None,
    browser_profile: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Playlist:
    """List an author's latest posts by profile/channel URL.

    Returns a Playlist of Results (one per post/track/video), up to `limit`.
    Supported: SoundCloud, VK, Twitch, Tumblr, Rutube, Vimeo, Pinterest,
    Twitter/X, Instagram. Same auth params as extract().
    """
    url = url.strip()
    service = _match_profile(url)
    if service is None:
        raise UnsupportedUrlError(
            f"not a supported profile/channel URL: {url}")
    ctx = _build_ctx(timeout, proxies, session, cookies, cookies_from_browser,
                     browser_profile, service.SERVICE)
    return service.extract_timeline(ctx, url, limit)
