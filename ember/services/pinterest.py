"""Pinterest: pins with video or image.

Method — parse the pin page HTML: video lives on v1.pinimg.com (mp4 or
HLS), images on i.pinimg.com. Short pin.it links are resolved via redirect.
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context, gather
from ..models import Media, Result, safe_filename

SERVICE = "pinterest"

PATTERNS = [
    re.compile(r"https?://(?:[\w-]+\.)?pinterest\.[\w.]+/pin/(\d+)"),
    re.compile(r"https?://pin\.it/([\w]+)"),
]

PROFILE_PATTERNS = [
    re.compile(r"https?://(?:[\w-]+\.)?pinterest\.[\w.]+/"
               r"(?!(?:pin|ideas|today|search|settings)/)"
               r"([\w-]+)(?:/([\w-]+))?/?$"),
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

    # карусель: несколько картинок в carousel_data
    cm = re.search(r'"carousel_slots"\s*:\s*(\[.*?\}\s*\])', html, re.DOTALL)
    if cm:
        seen, media = set(), []
        for u in re.findall(r'https://i\.pinimg\.com/originals/[^"\\]+?\.(?:jpg|png)',
                            cm.group(1)):
            u = u.replace("\\/", "/")
            if u not in seen:
                seen.add(u)
                ext = "png" if u.endswith(".png") else "jpg"
                media.append(Media(kind="photo", url=u, ext=ext))
        if len(media) > 1:
            return Result(service=SERVICE, kind="gallery", media=media,
                          source_url=url, filename_hint=hint)

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


def extract_timeline(ctx: Context, url: str, limit: int = 30):
    """Pinterest user or board -> Playlist of its latest pins (via RSS feed)."""
    from ..models import Playlist
    m = PROFILE_PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("not a Pinterest user/board URL", SERVICE)
    user, board = m.group(1), m.group(2)
    rss = (f"https://www.pinterest.com/{user}/{board}.rss" if board
           else f"https://www.pinterest.com/{user}/feed.rss")
    r = ctx.get(rss)
    if r.status_code != 200:
        raise ExtractionError(f"could not read Pinterest feed (HTTP {r.status_code})", SERVICE)
    seen = dict.fromkeys(re.findall(r"/pin/(\d+)", r.text))  # dedup, keep order
    urls = [f"https://www.pinterest.com/pin/{p}/" for p in seen][:limit]
    entries = gather(lambda u: extract(ctx, u), urls)
    if not entries:
        raise ExtractionError("no pins in this Pinterest feed", SERVICE)
    return Playlist(service=SERVICE, entries=entries, author=user, source_url=url)
