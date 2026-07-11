"""Bluesky: video (HLS) and images from posts.

Method — the public XRPC endpoint getPostThread, no auth needed.
Video is served as an HLS playlist, images as direct CDN links.
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename, to_timestamp

SERVICE = "bluesky"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?bsky\.app/profile/([^/]+)/post/([\w]+)"),
]

_API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"


def extract(ctx: Context, url: str) -> Result:
    m = PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("could not parse Bluesky link", SERVICE)
    handle, rkey = m.group(1), m.group(2)

    at_uri = f"at://{handle}/app.bsky.feed.post/{rkey}"
    r = ctx.get(_API, params={"uri": at_uri, "depth": 0, "parentHeight": 0})
    if r.status_code != 200:
        raise ExtractionError(
            f"Bluesky API returned HTTP {r.status_code} (post deleted or profile hidden)",
            SERVICE)
    try:
        post = r.json()["thread"]["post"]
    except (ValueError, LookupError) as e:
        raise ExtractionError(f"unexpected Bluesky response: {e}", SERVICE) from e

    author = (post.get("author") or {}).get("handle") or handle
    record = post.get("record") or {}
    title = (record.get("text") or "").strip() or None
    hint = safe_filename(f"bluesky_{author}_{rkey}")
    embed = post.get("embed") or {}
    etype = embed.get("$type", "")

    def result(kind, media, thumbnail=None):
        return Result(service=SERVICE, kind=kind, media=media, title=title,
                      author=author, source_url=url, filename_hint=hint,
                      thumbnail=thumbnail, timestamp=to_timestamp(record.get("createdAt")),
                      like_count=post.get("likeCount"))

    # видео
    if "video" in etype:
        playlist = embed.get("playlist")
        if playlist:
            # video.bsky.app/watch/... -> video.cdn.bsky.app/hls/...
            playlist = playlist.replace(
                "video.bsky.app/watch/", "video.cdn.bsky.app/hls/")
            return result("single", [Media(kind="video", url=playlist, ext="m3u8")],
                          thumbnail=embed.get("thumbnail"))

    # картинки
    images = embed.get("images") or []
    if images:
        media = [Media(kind="photo", url=img["fullsize"], ext="jpg")
                 for img in images if img.get("fullsize")]
        if media:
            return result("gallery" if len(media) > 1 else "single", media)

    # GIF из внешнего эмбеда (tenor)
    external = embed.get("external") or {}
    ext_uri = external.get("uri", "")
    if "media.tenor.com" in ext_uri:
        gif_url = ext_uri.split("?")[0]
        return result("single", [Media(kind="gif", url=gif_url, ext="gif")])

    raise ExtractionError("no video or images in the post", SERVICE)
