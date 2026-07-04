"""Facebook: видео и Reels.

Метод — парсинг HTML публичной страницы: ссылки на mp4 лежат в полях
browser_native_hd_url / browser_native_sd_url. Facebook агрессивно
блокирует ботов, поэтому многие видео открываются только с cookies
аккаунта: extract(url, cookies_from_browser="firefox") или cookies={...}.
"""

from __future__ import annotations

import json
import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename

SERVICE = "facebook"

PATTERNS = [
    re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/reel/(\d+)"),
    re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/watch/?\?v=(\d+)"),
    re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/[^/]+/videos/(?:[^/]+/)?(\d+)"),
    re.compile(r"https?://(?:www\.)?facebook\.com/share/[rv]/([\w-]+)"),
    re.compile(r"https?://fb\.watch/([\w-]+)"),
]

_HD_RE = re.compile(r'"browser_native_hd_url":("(?:[^"\\]|\\.)*")')
_SD_RE = re.compile(r'"browser_native_sd_url":("(?:[^"\\]|\\.)*")')
_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def extract(ctx: Context, url: str) -> Result:
    # для короткой ссылки / share — раскроем и заодно возьмём финальный URL
    if "fb.watch" in url or "/share/" in url:
        url = ctx.get(url, allow_redirects=True, headers=_HEADERS).url

    ident = None
    for p in PATTERNS:
        m = p.search(url)
        if m:
            ident = m.group(1)
            break

    r = ctx.get(url, headers=_HEADERS)
    if r.status_code != 200:
        raise ExtractionError(f"Facebook returned HTTP {r.status_code}", SERVICE)
    html = r.text

    m = _HD_RE.search(html) or _SD_RE.search(html)
    if not m:
        raise ExtractionError(
            "no video URL found. Facebook often serves it only to logged-in "
            "users: pass cookies_from_browser=\"firefox\" or cookies={...}", SERVICE)
    try:
        video_url = json.loads(m.group(1))
    except ValueError:
        video_url = m.group(1).strip('"').encode().decode("unicode_escape")

    title_m = _TITLE_RE.search(html)
    title = title_m.group(1) if title_m else None
    hint = safe_filename(f"facebook_{ident or 'video'}")

    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=video_url, ext="mp4")],
        title=title, source_url=url, filename_hint=hint)
