"""TikTok: video, photo posts (slideshows) and music.

Method (like cobalt): open the video page with a normal browser User-Agent
and parse the JSON from the tag
<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">.
Downloading from the CDN needs the same cookies — placed in http_headers.
"""

from __future__ import annotations

import json
import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, Subtitle, safe_filename, to_timestamp

SERVICE = "tiktok"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?tiktok\.com/@[^/]+/(?:video|photo)/(\d+)"),
    re.compile(r"https?://(?:www\.)?tiktok\.com/(?:v|t)/([\w.-]+)"),
    re.compile(r"https?://(?:vm|vt)\.tiktok\.com/([\w.-]+)"),
]

_REHYDRATION_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _resolve_post_id(ctx: Context, url: str) -> str:
    m = re.search(r"tiktok\.com/@[^/]+/(?:video|photo)/(\d+)", url)
    if m:
        return m.group(1)
    # короткая ссылка — идём по редиректам
    r = ctx.get(url, allow_redirects=True)
    m = re.search(r"/(?:video|photo)/(\d+)", r.url)
    if not m:
        raise ExtractionError(
            f"could not determine post id from link {url}", SERVICE)
    return m.group(1)


def extract(ctx: Context, url: str) -> Result:
    post_id = _resolve_post_id(ctx, url)

    page = ctx.get(f"https://www.tiktok.com/@i/video/{post_id}")
    m = _REHYDRATION_RE.search(page.text)
    if not m:
        raise ExtractionError(
            "page has no __UNIVERSAL_DATA_FOR_REHYDRATION__ "
            "(TikTok may have shown a captcha)", SERVICE)

    try:
        data = json.loads(m.group(1))
        detail = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]
        item = detail["itemInfo"]["itemStruct"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ExtractionError(f"unexpected data structure: {e}", SERVICE) from e

    author = (item.get("author") or {}).get("uniqueId")
    title = (item.get("desc") or "").strip() or None
    hint = safe_filename(f"tiktok_{author or 'video'}_{post_id}")

    # cookies обязательны для скачивания с CDN TikTok
    dl_headers = {
        "User-Agent": ctx.session.headers.get("User-Agent", ""),
        "Referer": "https://www.tiktok.com/",
    }
    cookie = ctx.cookie_header("tiktok.com")
    if cookie:
        dl_headers["Cookie"] = cookie

    image_post = item.get("imagePost")
    if image_post:
        media = []
        for img in image_post.get("images", []):
            urls = (img.get("imageURL") or {}).get("urlList") or []
            if urls:
                media.append(Media(
                    kind="photo", url=urls[0], ext="jpg",
                    http_headers=dict(dl_headers)))
        music_url = (item.get("music") or {}).get("playUrl")
        if music_url:
            media.append(Media(
                kind="audio", url=music_url, ext="mp3",
                http_headers=dict(dl_headers)))
        if not media:
            raise ExtractionError("photo post has no images", SERVICE)
        return Result(
            service=SERVICE, kind="gallery", media=media,
            title=title, author=author, source_url=url, filename_hint=hint)

    video = item.get("video") or {}
    play_addr = video.get("playAddr")
    if not play_addr:
        raise ExtractionError(
            "post has no video URL (deleted or region-blocked)",
            SERVICE)

    quality = None
    if video.get("height"):
        quality = f"{video['height']}p"

    subtitles = []
    for sub in video.get("subtitleInfos") or []:
        if "webvtt" in (sub.get("Format") or "").lower() and sub.get("Url"):
            subtitles.append(Subtitle(
                lang=sub.get("LanguageCodeName") or sub.get("LanguageID") or "sub",
                url=sub["Url"], ext="vtt"))

    return Result(
        service=SERVICE,
        kind="single",
        media=[Media(
            kind="video", url=play_addr, ext="mp4",
            quality=quality, http_headers=dl_headers)],
        title=title,
        author=author,
        source_url=url,
        filename_hint=hint,
        thumbnail=video.get("cover") or video.get("originCover"),
        duration=video.get("duration"),
        timestamp=to_timestamp(item.get("createTime")),
        view_count=(item.get("stats") or {}).get("playCount"),
        like_count=(item.get("stats") or {}).get("diggCount"),
        subtitles=subtitles,
    )
