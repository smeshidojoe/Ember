"""Bluesky: video (HLS) and images from posts.

Method — the public XRPC endpoint getPostThread, no auth needed.
Video is served as an HLS playlist.

Images: the `fullsize` link the API hands out points at the CDN, which
re-encodes to WebP — same pixel size, but measured 1.3x–12x smaller than the
file the author uploaded. The post record also carries the blob reference of
that original, so we fetch it through com.atproto.sync.getBlob instead and
hand back the untouched upload.
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
_BLOB_API = "https://bsky.social/xrpc/com.atproto.sync.getBlob"


def _record_images(record: dict) -> list:
    """Image entries of the raw record — these hold the original blob refs."""
    embed = record.get("embed") or {}
    if "recordWithMedia" in embed.get("$type", ""):     # цитата + картинки
        embed = embed.get("media") or {}
    return embed.get("images") or []


def _original(did: str, entry: dict):
    """(url, ext) исходника из записи, либо None если ссылки на блоб нет."""
    blob = entry.get("image") or {}
    cid = (blob.get("ref") or {}).get("$link")
    if not (did and cid):
        return None
    # mimeType сообщает настоящий формат: CDN-ссылка всегда webp, а загружали
    # чаще jpeg или png, и расширение файла должно совпадать с содержимым
    mime = blob.get("mimeType") or "image/jpeg"
    ext = mime.rsplit("/", 1)[-1].lower()
    ext = {"jpeg": "jpg"}.get(ext, ext)
    return f"{_BLOB_API}?did={did}&cid={cid}", ext


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

    # картинки: берём исходник из записи, CDN-ссылка — запасной вариант
    images = embed.get("images") or []
    if images:
        did = (post.get("author") or {}).get("did")
        originals = _record_images(record)
        media = []
        for i, img in enumerate(images):
            orig = _original(did, originals[i]) if i < len(originals) else None
            if orig:
                media.append(Media(kind="photo", url=orig[0], ext=orig[1]))
            elif img.get("fullsize"):
                media.append(Media(kind="photo", url=img["fullsize"], ext="jpg"))
        if media:
            return result("gallery" if len(media) > 1 else "single", media)

    # GIF из внешнего эмбеда (tenor)
    external = embed.get("external") or {}
    ext_uri = external.get("uri", "")
    if "media.tenor.com" in ext_uri:
        gif_url = ext_uri.split("?")[0]
        return result("single", [Media(kind="gif", url=gif_url, ext="gif")])

    raise ExtractionError("no video or images in the post", SERVICE)
