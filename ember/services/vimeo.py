"""Vimeo: video.

Method — the public player config (player.vimeo.com/video/<id>/config),
the same JSON the built-in player uses. No OAuth. Returns either progressive
mp4s (we pick the best quality) or an HLS master (.m3u8).
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context, gather
from ..models import Media, MediaVariant, Result, Subtitle, safe_filename

SERVICE = "vimeo"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?vimeo\.com/(?:channels/[\w]+/|groups/[\w]+/videos/|album/\d+/video/)?(\d+)(?:/(\w+))?"),
    re.compile(r"https?://player\.vimeo\.com/video/(\d+)(?:\?h=(\w+))?"),
]

PROFILE_PATTERNS = [
    re.compile(r"https?://(?:www\.)?vimeo\.com/([a-zA-Z][\w]*)/?$"),
]


def _subtitles(text_tracks: list) -> list:
    subs = []
    for t in text_tracks:
        u = t.get("url")
        if not u:
            continue
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = "https://vimeo.com" + u
        subs.append(Subtitle(lang=t.get("lang") or "sub", url=u, ext="vtt"))
    return subs


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
    request = data.get("request") or {}
    files = request.get("files") or {}
    subs = _subtitles(request.get("text_tracks") or [])

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
            thumbnail=thumb, subtitles=subs)

    hls = (files.get("hls") or {}).get("cdns") or {}
    for cdn in hls.values():
        if cdn.get("url"):
            return Result(
                service=SERVICE, kind="single",
                media=[Media(kind="video", url=cdn["url"], ext="m3u8")],
                title=title, author=author, source_url=url, filename_hint=hint,
                thumbnail=thumb, subtitles=subs)

    raise ExtractionError("Vimeo response has neither mp4 nor HLS stream", SERVICE)


def extract_timeline(ctx: Context, url: str, limit: int = 30):
    """Vimeo user -> Playlist of their latest videos (v2 simple API)."""
    from ..models import Playlist
    m = PROFILE_PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("not a Vimeo user URL", SERVICE)
    user = m.group(1)
    r = ctx.get(f"https://vimeo.com/api/v2/{user}/videos.json")
    if r.status_code != 200:
        raise ExtractionError(f"could not list Vimeo user (HTTP {r.status_code})", SERVICE)
    try:
        videos = r.json()
    except ValueError as e:
        raise ExtractionError(f"unexpected Vimeo response: {e}", SERVICE) from e
    urls = [f"https://vimeo.com/{v['id']}" for v in videos[:limit] if v.get("id")]
    entries = gather(lambda u: extract(ctx, u), urls)
    if not entries:
        raise ExtractionError("no videos for this Vimeo user", SERVICE)
    return Playlist(service=SERVICE, entries=entries, author=user, source_url=url)
