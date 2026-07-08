"""Tumblr: video and audio from posts.

Method — the public mobile API (api-http2.tumblr.com) with a built-in API
key (the same one embedded in the app and used by cobalt).
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename

SERVICE = "tumblr"

# jrsCWX... — публичный ключ мобильного приложения Tumblr (как у cobalt)
_API_KEY = "jrsCWX1XDuVxAFO4GkK147syAoN8BJZ5voz8tS80bPcj26Vc5Z"
_MOBILE_UA = "Tumblr/iPhone/33.3/320/13.0"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?tumblr\.com/([\w-]+)/(\d+)"),
    re.compile(r"https?://([\w-]+)\.tumblr\.com/post/(\d+)"),
]


def _parse(url: str):
    for p in PATTERNS:
        m = p.match(url)
        if m:
            return m.group(1), m.group(2)
    raise ExtractionError("could not parse Tumblr link", SERVICE)


def _iter_content(element: dict):
    """Post content + reblogged trail content (like cobalt)."""
    yield from element.get("content") or []
    for trail in element.get("trail") or []:
        yield from trail.get("content") or []


def extract(ctx: Context, url: str) -> Result:
    domain, post_id = _parse(url)

    api = f"https://api-http2.tumblr.com/v2/blog/{domain}/posts/{post_id}/permalink"
    r = ctx.get(api, params={"api_key": _API_KEY},
                headers={"User-Agent": _MOBILE_UA})
    if r.status_code != 200:
        raise ExtractionError(
            f"Tumblr API returned HTTP {r.status_code} (post deleted or blog private)",
            SERVICE)
    try:
        elements = r.json()["response"]["timeline"]["elements"]
        element = elements[0]
    except (ValueError, LookupError) as e:
        raise ExtractionError(f"unexpected Tumblr response: {e}", SERVICE) from e

    author = (element.get("blog") or {}).get("name") or domain
    title = element.get("summary") or None
    hint = safe_filename(f"tumblr_{author}_{post_id}")

    def result(kind, media):
        return Result(service=SERVICE, kind=kind, media=media, title=title,
                      author=author, source_url=url, filename_hint=hint)

    # --- новый формат NPF (element.content / trail) — собираем ВСЕ блоки ---
    npf = []
    for c in _iter_content(element):
        if not isinstance(c, dict):
            continue
        ctype = c.get("type")
        if ctype == "video":
            u = (c.get("media") or {}).get("url")
            if u:
                npf.append(Media(kind="video", url=u, ext="mp4"))
        elif ctype == "audio":
            u = (c.get("media") or {}).get("url")
            if u:
                npf.append(Media(kind="audio", url=u, ext="mp3"))
        elif ctype == "image":
            media_list = c.get("media") or []
            if media_list and media_list[0].get("url"):
                npf.append(Media(kind="photo", url=media_list[0]["url"], ext="jpg"))
    if npf:
        return result("gallery" if len(npf) > 1 else "single", npf)

    # --- legacy-формат (старые посты: video_url / photos / audio_url) ---
    legacy_type = element.get("type")
    if legacy_type == "video" and element.get("video_url"):
        return result("single", [Media(kind="video", url=element["video_url"], ext="mp4")])
    if legacy_type == "audio":
        au = element.get("audio_url") or element.get("audio_source_url")
        if au:
            return result("single", [Media(kind="audio", url=au, ext="mp3")])
    if legacy_type == "photo" and element.get("photos"):
        media = []
        for ph in element["photos"]:
            src = (ph.get("original_size") or {}).get("url")
            if src:
                ext = "gif" if src.endswith(".gif") else "jpg"
                kind = "gif" if ext == "gif" else "photo"
                media.append(Media(kind=kind, url=src, ext=ext))
        if media:
            return result("gallery" if len(media) > 1 else "single", media)

    raise ExtractionError("no video, audio or image in the post", SERVICE)
