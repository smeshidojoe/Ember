"""Pinterest: пины с видео или картинкой.

Метод — парсинг HTML страницы пина: видео лежит на v1.pinimg.com
(mp4 или HLS), картинки — на i.pinimg.com. Короткие ссылки pin.it
раскрываются редиректом.
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename

SERVICE = "pinterest"

PATTERNS = [
    re.compile(r"https?://(?:[\w-]+\.)?pinterest\.[\w.]+/pin/(\d+)"),
    re.compile(r"https?://pin\.it/([\w]+)"),
]

_VIDEO_RE = re.compile(r'"url":"(https://v1\.pinimg\.com/videos/[^"]+?\.mp4)"')
_HLS_RE = re.compile(r'"url":"(https://v1\.pinimg\.com/videos/[^"]+?\.m3u8)"')
_IMG_RE = re.compile(r'"(https://i\.pinimg\.com/originals/[^"]+?\.(?:jpg|png|gif))"')


def _resolve_id(ctx: Context, url: str) -> str:
    m = re.search(r"/pin/(\d+)", url)
    if m:
        return m.group(1)
    r = ctx.get(url, allow_redirects=True)
    m = re.search(r"/pin/(\d+)", r.url)
    if not m:
        raise ExtractionError(f"could not determine pin id from {url}", SERVICE)
    return m.group(1)


def extract(ctx: Context, url: str) -> Result:
    pin_id = _resolve_id(ctx, url)
    html = ctx.get(f"https://www.pinterest.com/pin/{pin_id}/").text
    if '"PinNotFound"' in html:
        raise ExtractionError("pin not found or deleted", SERVICE)

    hint = safe_filename(f"pinterest_{pin_id}")

    m = _VIDEO_RE.search(html)
    if m:
        video_url = m.group(1).replace("\\/", "/")
        return Result(service=SERVICE, kind="single",
                      media=[Media(kind="video", url=video_url, ext="mp4")],
                      source_url=url, filename_hint=hint)

    m = _HLS_RE.search(html)
    if m:
        hls_url = m.group(1).replace("\\/", "/")
        return Result(service=SERVICE, kind="single",
                      media=[Media(kind="video", url=hls_url, ext="m3u8")],
                      source_url=url, filename_hint=hint)

    m = _IMG_RE.search(html)
    if m:
        img_url = m.group(1).replace("\\/", "/")
        ext = "gif" if img_url.endswith(".gif") else ("png" if img_url.endswith(".png") else "jpg")
        kind = "gif" if ext == "gif" else "photo"
        return Result(service=SERVICE, kind="single",
                      media=[Media(kind=kind, url=img_url, ext=ext)],
                      source_url=url, filename_hint=hint)

    raise ExtractionError("no media found on the pin page", SERVICE)
