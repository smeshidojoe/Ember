"""Read cookies straight from a browser — like yt-dlp --cookies-from-browser.

Preference order:
1. built-in reader (_browser_cookies): Firefox (any OS) and the Chromium
   family on Windows/macOS/Linux — no required dependencies;
2. yt-dlp (if installed) — covers what the built-in reader can't;
3. browser_cookie3 — last resort.

Returns a plain dict {name: value}. Fallback backends are optional: if none
covers the combination, EmberError is raised.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import _browser_cookies as native
from .errors import EmberError

log = logging.getLogger(__name__)

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
    """Built-in reader. None if the combination is unsupported (need a
    fallback). EmberError (e.g. App-Bound Encryption) propagates out."""
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
    """Read cookies from the given browser.

    Args:
        browser: "firefox", "vivaldi", "chrome", "edge", "brave", "opera", ...
        service: an Ember service name — only its domains are read.
                 If None, domains of all services are read.
        profile: browser profile name.

    Returns:
        dict {cookie_name: value}.

    Raises:
        EmberError: the combination is uncovered by the built-in reader and no
                    fallback backend (yt-dlp / browser_cookie3) is installed;
                    or the browser is under App-Bound Encryption.
    """
    browser = browser.lower().strip()
    if service:
        domains = _DOMAIN_HINTS.get(service, [])
    else:
        domains = [d for lst in _DOMAIN_HINTS.values() for d in lst]

    try:
        result = _via_native(browser, profile, domains)
        backend = "native"
        if result is None:
            result = _via_ytdlp(browser, profile, domains)
            backend = "yt-dlp"
        if result is None:
            result = _via_browser_cookie3(browser, domains)
            backend = "browser_cookie3"
    except EmberError:
        raise
    except PermissionError as e:  # браузер открыт и держит cookie-базу
        raise EmberError(
            f"could not read cookies: {browser} is running and locks its cookie "
            f"database. Close {browser} and retry, or use --cookies-file / "
            "--cookies instead") from e
    except Exception as e:  # прочее неожиданное — без сырого traceback
        raise EmberError(f"could not read cookies from {browser}: {e}") from e
    if result is not None:
        log.info("cookies from %s via %s: %d cookies", browser, backend, len(result))
    if result is None:
        raise EmberError(
            f"could not read cookies from {browser} on this OS: our reader "
            "doesn't cover it and neither yt-dlp nor browser_cookie3 is "
            "installed. Use Firefox, export --cookies-file, pass --cookies "
            "manually, or `pip install yt-dlp`")
    return result
