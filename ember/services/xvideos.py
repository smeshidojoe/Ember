"""XVideos: videos.

Method — the video page defines `html5player.setVideoHLS('...')` (an HLS
master with every quality) plus progressive mp4 fallbacks and metadata.
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, MediaVariant, Result, safe_filename, to_timestamp

SERVICE = "xvideos"

# любой хост, содержащий xvideos (зеркала/поддомены/TLD)
PATTERNS = [
    re.compile(r"https?://[^/]*xvideos[^/]*/(video[\w.]*/\d+/\d+/[\w-]+)"),
    re.compile(r"https?://[^/]*xvideos[^/]*/(video[\w.]+)"),
    re.compile(r"https?://[^/]*xvideos[^/]*/(embedframe/\w+)"),
]

_HLS_RE = re.compile(r"html5player\.setVideoHLS\('([^']+)'\)")
_HIGH_RE = re.compile(r"html5player\.setVideoUrlHigh\('([^']+)'\)")
_LOW_RE = re.compile(r"html5player\.setVideoUrlLow\('([^']+)'\)")
_TITLE_RE = re.compile(r"html5player\.setVideoTitle\('([^']*)'\)")
_THUMB_RE = re.compile(r"html5player\.setThumbUrl\('([^']*)'\)")
_UPLOADER_RE = re.compile(r"setUploaderName\('([^']*)'\)")
_DATE_RE = re.compile(r'"uploadDate"\s*:\s*"([^"]*)"')
_DURATION_RE = re.compile(r'"duration"\s*:\s*"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"')


def _duration(html: str):
    m = _DURATION_RE.search(html)
    if not m:
        return None
    h, mi, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + s or None


def extract(ctx: Context, url: str) -> Result:
    if not any(p.match(url) for p in PATTERNS):
        raise ExtractionError("could not parse XVideos link", SERVICE)

    r = ctx.get(url)
    if r.status_code != 200:
        raise ExtractionError(
            f"XVideos returned HTTP {r.status_code} (removed, private, or geo-blocked)",
            SERVICE)
    html = r.text

    m = _TITLE_RE.search(html)
    title = m.group(1) if m else None
    m = _UPLOADER_RE.search(html)
    author = m.group(1) if m else None
    m = _THUMB_RE.search(html)
    thumb = m.group(1) if m else None
    m = _DATE_RE.search(html)
    timestamp = to_timestamp(m.group(1)) if m else None

    ident = re.sub(r"\W+", "_", url.rstrip("/").rsplit("/", 1)[-1])[:60]
    hint = safe_filename(f"xvideos_{ident}_{title or ''}")

    def result(media):
        return Result(service=SERVICE, kind="single", media=[media], title=title,
                      author=author, source_url=url, filename_hint=hint,
                      thumbnail=thumb, duration=_duration(html), timestamp=timestamp)

    # HLS master несёт все качества — предпочитаем его
    m = _HLS_RE.search(html)
    if m:
        return result(Media(kind="video", url=m.group(1), ext="m3u8"))

    # прогрессивные mp4 как запасной путь
    variants = []
    for rx, quality, height in ((_HIGH_RE, "high", 720), (_LOW_RE, "low", 360)):
        mm = rx.search(html)
        if mm:
            variants.append(MediaVariant(url=mm.group(1), height=height,
                                         quality=quality, ext="mp4"))
    if variants:
        best = variants[0]
        return result(Media(kind="video", url=best.url, ext="mp4",
                            quality=best.quality, variants=variants))

    raise ExtractionError(
        "no video stream on the page (removed, or members-only)", SERVICE)
