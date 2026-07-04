"""Newgrounds: видео (portal) и аудио (audio/listen).

Видео: JSON-эндпоинт /portal/video/<id> (нужен заголовок
X-Requested-With). Аудио: параметры вшиты в HTML страницы прослушивания.
"""

from __future__ import annotations

import json
import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename

SERVICE = "newgrounds"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?newgrounds\.com/portal/view/(\d+)"),
    re.compile(r"https?://(?:www\.)?newgrounds\.com/audio/listen/(\d+)"),
]


def _extract_video(ctx: Context, media_id: str, url: str) -> Result:
    r = ctx.get(f"https://www.newgrounds.com/portal/video/{media_id}",
                headers={"X-Requested-With": "XMLHttpRequest"})
    if r.status_code != 200:
        raise ExtractionError(f"Newgrounds returned HTTP {r.status_code}", SERVICE)
    data = r.json()
    sources = data.get("sources") or {}
    if not sources:
        raise ExtractionError("video has no sources (18+ or deleted)", SERVICE)
    # ключи качества вида "1080p", "720p" — берём наибольший
    best_q = max(sources, key=lambda q: int(re.sub(r"\D", "", q) or 0))
    src_list = sources[best_q]
    if not src_list or not src_list[0].get("src"):
        raise ExtractionError("empty video source list", SERVICE)

    title = (data.get("title") or "").strip() or None
    author = data.get("author")
    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=src_list[0]["src"], ext="mp4", quality=best_q)],
        title=title, author=author, source_url=url,
        filename_hint=safe_filename(f"newgrounds_{media_id}_{title or ''}"))


def _extract_audio(ctx: Context, media_id: str, url: str) -> Result:
    html = ctx.get(f"https://www.newgrounds.com/audio/listen/{media_id}").text
    try:
        chunk = html.split(',"params":{')[1].split(',"images":')[0]
        params = json.loads("{" + chunk)
    except (IndexError, ValueError) as e:
        raise ExtractionError(f"could not parse the audio page: {e}", SERVICE) from e

    file_url = params.get("filename")
    if not file_url:
        raise ExtractionError("no audio file URL on the page", SERVICE)
    file_url = file_url.replace("\\/", "/")
    title = params.get("name")
    author = params.get("artist")
    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="audio", url=file_url, ext="mp3")],
        title=title, author=author, source_url=url,
        filename_hint=safe_filename(f"newgrounds_{author or ''}_{title or media_id}"))


def extract(ctx: Context, url: str) -> Result:
    m = PATTERNS[0].match(url)
    if m:
        return _extract_video(ctx, m.group(1), url)
    m = PATTERNS[1].match(url)
    if m:
        return _extract_audio(ctx, m.group(1), url)
    raise ExtractionError("could not parse Newgrounds link", SERVICE)
