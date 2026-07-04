"""Извлечение cookies прямо из браузера — по аналогии с
yt-dlp --cookies-from-browser.

Порядок предпочтений:
1. yt-dlp (если установлен) — самый надёжный, умеет расшифровывать
   cookies новых версий Chrome (App-Bound Encryption);
2. browser_cookie3 — запасной вариант.

Возвращается обычный dict {name: value}. Оба бэкенда — необязательные:
если ни одного нет, поднимается EmberError с понятным сообщением.
"""

from __future__ import annotations

from typing import Optional

from .errors import EmberError

# домены, cookies которых имеет смысл тянуть под каждый сервис
_DOMAIN_HINTS = {
    "twitter": ["x.com", "twitter.com"],
    "instagram": ["instagram.com"],
    "tiktok": ["tiktok.com"],
    "reddit": ["reddit.com"],
}


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
                f"не удалось прочитать cookies из {browser}: современные "
                "Chrome/Edge/Brave шифруют их (App-Bound Encryption) так, "
                "что их не читает даже yt-dlp. Варианты: используйте Firefox "
                "(--cookies-from-browser firefox), либо экспортируйте "
                "cookies.txt расширением браузера и передайте --cookies-file, "
                "либо вручную --cookies \"auth_token=...; ct0=...\"") from e
        raise EmberError(f"не удалось прочитать cookies из {browser}: {msg}") from e
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
        raise EmberError(f"browser_cookie3 не знает браузер '{browser}'")
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
        browser: "chrome", "firefox", "edge", "brave", "opera", ...
        service: имя сервиса Ember — тогда берутся только его домены.
                 Если None, тянутся домены всех сервисов.
        profile: имя/путь профиля браузера (только для бэкенда yt-dlp).

    Returns:
        dict {имя_cookie: значение}.

    Raises:
        EmberError: не установлен ни yt-dlp, ни browser_cookie3,
                    либо браузер не поддерживается.
    """
    browser = browser.lower().strip()
    if service:
        domains = _DOMAIN_HINTS.get(service, [])
    else:
        domains = [d for lst in _DOMAIN_HINTS.values() for d in lst]

    result = _via_ytdlp(browser, profile, domains)
    if result is None:
        result = _via_browser_cookie3(browser, domains)
    if result is None:
        raise EmberError(
            "чтобы брать cookies из браузера, нужен yt-dlp или browser_cookie3 "
            "(pip install yt-dlp). Либо передайте cookies вручную "
            "через cookies={...}")
    return result
