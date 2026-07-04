"""Vimeo: видео.

Метод — публичный player config (player.vimeo.com/video/<id>/config),
тот же JSON, что использует встроенный плеер. Без OAuth. Отдаёт либо
прогрессивные mp4 (выбираем лучшее качество), либо HLS-мастер (.m3u8).
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, MediaVariant, Result, safe_filename

SERVICE = "vimeo"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?vimeo\.com/(?:channels/[\w]+/|groups/[\w]+/videos/|album/\d+/video/)?(\d+)(?:/(\w+))?"),
    re.compile(r"https?://player\.vimeo\.com/video/(\d+)(?:\?h=(\w+))?"),
]


def _thumbnail(video: dict):
    thumbs = video.get("thumbs") or {}
    # thumbs = {"640": url, "960": url, "base": url}; берём самый большой
    if thumbs:
        numeric = {int(k): v for k, v in thumbs.items() if k.isdigit()}
        if numeric:
            return numeric[max(numeric)]
        return thumbs.get("base")
    return None


def _parse(url: str):
    for p in PATTERNS:
        m = p.match(url)
        if m:
            return m.group(1), m.group(2)
    raise ExtractionError("could not parse Vimeo link", SERVICE)


def extract(ctx: Context, url: str) -> Result:
    video_id, unlisted_hash = _parse(url)

    config_url = f"https://player.vimeo.com/video/{video_id}/config"
    if unlisted_hash:
        config_url += f"?h={unlisted_hash}"
    r = ctx.get(config_url, headers={"Referer": "https://vimeo.com/"})
    if r.status_code != 200:
        raise ExtractionError(
            f"Vimeo returned HTTP {r.status_code} (private video or "
            "domain restriction — try cookies)", SERVICE)
    try:
        data = r.json()
    except ValueError as e:
        raise ExtractionError(f"unexpected Vimeo response: {e}", SERVICE) from e

    video = data.get("video") or {}
    title = video.get("title")
    author = (video.get("owner") or {}).get("name")
    thumb = _thumbnail(video)
    hint = safe_filename(f"vimeo_{video_id}_{title or ''}")
    files = ((data.get("request") or {}).get("files")) or {}

    progressive = files.get("progressive") or []
    if progressive:
        ordered = sorted(progressive, key=lambda f: f.get("height") or 0, reverse=True)
        best = ordered[0]
        variants = [MediaVariant(url=f["url"], height=f.get("height"),
                                 quality=f.get("quality"), ext="mp4")
                    for f in ordered if f.get("url")]
        return Result(
            service=SERVICE, kind="single",
            media=[Media(kind="video", url=best["url"], ext="mp4",
                         quality=best.get("quality"), variants=variants)],
            title=title, author=author, source_url=url, filename_hint=hint,
            thumbnail=thumb)

    hls = (files.get("hls") or {}).get("cdns") or {}
    for cdn in hls.values():
        if cdn.get("url"):
            return Result(
                service=SERVICE, kind="single",
                media=[Media(kind="video", url=cdn["url"], ext="m3u8")],
                title=title, author=author, source_url=url, filename_hint=hint,
                thumbnail=thumb)

    raise ExtractionError("Vimeo response has neither mp4 nor HLS stream", SERVICE)
