"""Rutube: видео (HLS).

Метод — публичный API play/options, отдаёт HLS-мастер (.m3u8).
Приватные видео требуют ключ ?p=<key> из ссылки.
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename

SERVICE = "rutube"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?rutube\.ru/(?:video(?:/private)?|play/embed|shorts)/([0-9a-f]{32})"),
]


def extract(ctx: Context, url: str) -> Result:
    m = PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("could not parse Rutube link", SERVICE)
    video_id = m.group(1)

    params = {"no_404": "true", "referer": "", "pver": "v2"}
    key = re.search(r"[?&]p=([\w-]+)", url)
    if key:
        params["p"] = key.group(1)

    r = ctx.get(f"https://rutube.ru/api/play/options/{video_id}/", params=params)
    if r.status_code != 200:
        raise ExtractionError(
            f"Rutube API returned HTTP {r.status_code} (video deleted or private)",
            SERVICE)
    try:
        data = r.json()
        m3u8 = data["video_balancer"]["m3u8"]
    except (ValueError, KeyError) as e:
        raise ExtractionError(f"unexpected Rutube response: {e}", SERVICE) from e

    title = data.get("title")
    author = (data.get("author") or {}).get("name")
    thumb = data.get("thumbnail_url") or data.get("picture_url")
    hint = safe_filename(f"rutube_{video_id}_{title or ''}")

    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=m3u8, ext="m3u8")],
        title=title, author=author, source_url=url, filename_hint=hint,
        thumbnail=thumb)
