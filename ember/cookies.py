"""Извлечение cookies прямо из браузера — по аналогии с
yt-dlp --cookies-from-browser.

Порядок предпочтений:
1. свой ридер (_browser_cookies): Firefox (любая ОС) и Chromium-семейство
   на Windows — без обязательных зависимостей;
2. yt-dlp (если установлен) — покрывает то, что не умеет свой ридер
   (например Chromium на macOS/Linux через системный keyring);
3. browser_cookie3 — запасной вариант.

Возвращается обычный dict {name: value}. Fallback-бэкенды необязательные:
если для непокрытой комбинации ни одного нет, поднимается EmberError.
"""

from __future__ import annotations

from typing import Optional

from . import _browser_cookies as native
from .errors import EmberError

# домены, cookies которых имеет смысл тянуть под каждый сервис
_DOMAIN_HINTS = {
    "tiktok": ["tiktok.com"],
    "twitter": ["x.com", "twitter.com"],
    "instagram": ["instagram.com"],
    "reddit": ["reddit.com"],
    "vimeo": ["vimeo.com"],
    "soundcloud": ["soundcloud.com"],
    "pinterest": ["pinterest.com"],
    "tumblr": ["tumblr.com"],
    "bluesky": ["bsky.app", "bsky.social"],
    "newgrounds": ["newgrounds.com"],
    "rutube": ["rutube.ru"],
    "ok": ["ok.ru", "odnoklassniki.ru"],
    "vk": ["vk.com", "vkvideo.ru", "vk.ru"],
    "facebook": ["facebook.com"],
    "twitch": ["twitch.tv"],
}


def _via_native(browser: str, profile: Optional[str], domains) -> Optional[dict]:
    """Свой ридер. None — если комбинация нам не по силам (нужен fallback).
    EmberError (например, App-Bound Encryption) пробрасывается наружу."""
    try:
        return native.native_cookies(browser, profile, domains)
    except native.NativeUnsupported:
        return None


def _via_ytdlp(browser: str, profile: Optional[str], domains) -> Optional[dict]:
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
        from yt_dlp.utils import DownloadError
    except ImportError:
        return None
    try:
        jar = extract_cookies_from_browser(browser, profile=profile)
    except DownloadError as e:
        msg = str(e)
        if "DPAPI" in msg or "decrypt" in msg.lower():
            raise EmberError(
                f"could not read cookies from {browser}: modern "
                "Chrome/Edge/Brave encrypt them (App-Bound Encryption) so that "
                "even yt-dlp cannot read them. Options: use Firefox "
                "(--cookies-from-browser firefox), export cookies.txt with a "
                "browser extension and pass --cookies-file, or pass them "
                "manually with --cookies \"auth_token=...; ct0=...\"") from e
        raise EmberError(f"could not read cookies from {browser}: {msg}") from e
    except Exception as e:  # прочие ошибки yt-dlp (напр. safari на Windows)
        raise EmberError(f"could not read cookies from {browser}: {e}") from e
    out = {}
    for c in jar:
        if any(d in (c.domain or "") for d in domains):
            out[c.name] = c.value
    return out


def _via_browser_cookie3(browser: str, domains) -> Optional[dict]:
    try:
        import browser_cookie3
    except ImportError:
        return None
    loader = getattr(browser_cookie3, browser, None)
    if loader is None:
        raise EmberError(f"browser_cookie3 does not know browser '{browser}'")
    out = {}
    for domain in domains:
        for c in loader(domain_name=domain):
            out[c.name] = c.value
    return out


def cookies_from_browser(
    browser: str,
    service: Optional[str] = None,
    profile: Optional[str] = None,
) -> dict:
    """Достаёт cookies из указанного браузера.

    Args:
        browser: "firefox", "vivaldi", "chrome", "edge", "brave", "opera", ...
        service: имя сервиса Ember — тогда берутся только его домены.
                 Если None, тянутся домены всех сервисов.
        profile: имя профиля браузера.

    Returns:
        dict {имя_cookie: значение}.

    Raises:
        EmberError: комбинацию не покрыл свой ридер, а fallback-бэкендов
                    (yt-dlp / browser_cookie3) нет; либо браузер под ABE.
    """
    browser = browser.lower().strip()
    if service:
        domains = _DOMAIN_HINTS.get(service, [])
    else:
        domains = [d for lst in _DOMAIN_HINTS.values() for d in lst]

    result = _via_native(browser, profile, domains)
    if result is None:
        result = _via_ytdlp(browser, profile, domains)
    if result is None:
        result = _via_browser_cookie3(browser, domains)
    if result is None:
        raise EmberError(
            f"could not read cookies from {browser} on this OS: our reader "
            "doesn't cover it and neither yt-dlp nor browser_cookie3 is "
            "installed. Use Firefox, export --cookies-file, pass --cookies "
            "manually, or `pip install yt-dlp`")
    return result
