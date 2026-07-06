"""Reddit: video (v.redd.it), GIFs, images and galleries.

Method (like cobalt): the public JSON endpoint
https://www.reddit.com/comments/<id>.json — no auth.
On v.redd.it, video and audio are separate DASH files, so video-with-sound
returns kind="merge" (needs ffmpeg).
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename

SERVICE = "reddit"

PATTERNS = [
    re.compile(r"https?://(?:www\.|old\.|new\.|np\.)?reddit\.com/r/[^/]+/comments/([a-z0-9]+)"),
    re.compile(r"https?://(?:www\.)?reddit\.com/comments/([a-z0-9]+)"),
    re.compile(r"https?://redd\.it/([a-z0-9]+)"),
    re.compile(r"https?://(?:www\.)?reddit\.com/r/[^/]+/s/([A-Za-z0-9]+)"),  # share-ссылки
    re.compile(r"https?://v\.redd\.it/([a-z0-9]+)"),
]


def _resolve_post_id(ctx: Context, url: str) -> str:
    m = re.search(r"reddit\.com/(?:r/[^/]+/)?comments/([a-z0-9]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"redd\.it/([a-z0-9]+)$", url.rstrip("/"))
    if m and "v.redd.it" not in url:
        return m.group(1)
    # share-ссылка или v.redd.it — раскручиваем редиректы
    r = ctx.get(url, allow_redirects=True)
    m = re.search(r"reddit\.com/(?:r/[^/]+/)?comments/([a-z0-9]+)", r.url)
    if not m:
        raise ExtractionError(
            f"could not determine post id from link {url}", SERVICE)
    return m.group(1)


def _find_audio(ctx: Context, fallback_url: str) -> str:
    """Find the audio track URL for v.redd.it (like cobalt: try HEADs)."""
    base = fallback_url.rsplit("/", 1)[0]
    for candidate in (
        f"{base}/DASH_AUDIO_128.mp4",
        f"{base}/DASH_AUDIO_64.mp4",
        f"{base}/DASH_audio.mp4",
        f"{base}/audio",
    ):
        if ctx.head_ok(candidate):
            return candidate
    return ""


def extract(ctx: Context, url: str) -> Result:
    post_id = _resolve_post_id(ctx, url)

    r = ctx.get(f"https://www.reddit.com/comments/{post_id}.json?raw_json=1")
    if r.status_code == 403:
        raise ExtractionError(
            "Reddit blocked anonymous access from this IP "
            "(\"blocked due to a network policy\" — common on VPN/hosting IPs). "
            "A different IP (proxy) or a home connection helps.", SERVICE)
    if r.status_code != 200:
        raise ExtractionError(
            f"Reddit returned HTTP {r.status_code} (post deleted or private subreddit)",
            SERVICE)

    try:
        post = r.json()[0]["data"]["children"][0]["data"]
    except (ValueError, LookupError) as e:
        raise ExtractionError(f"unexpected Reddit response: {e}", SERVICE) from e

    # кросспост: медиа лежит в оригинальном посте
    if not post.get("secure_media") and post.get("crosspost_parent_list"):
        parent = post["crosspost_parent_list"][0]
        for key in ("secure_media", "media_metadata", "is_gallery",
                    "url_overridden_by_dest", "preview"):
            if parent.get(key) is not None:
                post[key] = parent[key]

    title = post.get("title")
    author = post.get("author")
    hint = safe_filename(f"reddit_{post_id}_{title or ''}")

    def result(kind, media):
        return Result(service=SERVICE, kind=kind, media=media, title=title,
                      author=author, source_url=url, filename_hint=hint)

    # галерея
    if post.get("is_gallery") and post.get("media_metadata"):
        media = []
        for item in post["media_metadata"].values():
            source = item.get("s") or {}
            u = source.get("u") or source.get("gif")
            if u:
                ext = "gif" if source.get("gif") else "jpg"
                media.append(Media(kind="photo", url=u, ext=ext))
        if not media:
            raise ExtractionError("gallery has no images", SERVICE)
        return result("gallery", media)

    direct = post.get("url_overridden_by_dest") or post.get("url") or ""

    # гифка
    if direct.endswith(".gif"):
        return result("single", [Media(kind="gif", url=direct, ext="gif")])

    # видео v.redd.it
    reddit_video = ((post.get("secure_media") or {}).get("reddit_video")
                    or (post.get("preview") or {}).get("reddit_video_preview"))
    if reddit_video and reddit_video.get("fallback_url"):
        video_url = reddit_video["fallback_url"].split("?")[0]
        quality = f"{reddit_video['height']}p" if reddit_video.get("height") else None
        video = Media(kind="video", url=video_url, ext="mp4", quality=quality)
        audio_url = _find_audio(ctx, video_url)
        if audio_url:
            return result("merge", [video, Media(kind="audio", url=audio_url, ext="mp4")])
        return result("single", [video])

    # обычная картинка
    if post.get("post_hint") == "image" and direct:
        ext = "png" if direct.endswith(".png") else "jpg"
        return result("single", [Media(kind="photo", url=direct, ext=ext)])

    raise ExtractionError(
        "no supported media in the post (text or external link)", SERVICE)
