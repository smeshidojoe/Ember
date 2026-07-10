"""Rutube: video (HLS).

Method — the public play/options API, returns an HLS master (.m3u8).
Private videos require the ?p=<key> from the link.
"""

from __future__ import annotations

import re

from ..errors import ExtractionError
from ..http import Context, gather
from ..models import Media, Result, Subtitle, safe_filename

SERVICE = "rutube"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?rutube\.ru/(?:video(?:/private)?|play/embed|shorts)/([0-9a-f]{32})"),
]

PROFILE_PATTERNS = [
    re.compile(r"https?://(?:www\.)?rutube\.ru/(?:channel|u)/(\d+)/?"),
]


def extract(ctx: Context, url: str) -> Result:
    m = PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("could not parse Rutube link", SERVICE)
    video_id = m.group(1)

    params = {"no_404": "true", "referer": "", "pver": "v2"}
    key = re.search(r"[?&]p=([\w-]+)", url)
    if key:
        params["p"] = key.group(1)

    r = ctx.get(f"https://rutube.ru/api/play/options/{video_id}/", params=params)
    if r.status_code != 200:
        raise ExtractionError(
            f"Rutube API returned HTTP {r.status_code} (video deleted or private)",
            SERVICE)
    try:
        data = r.json()
        m3u8 = data["video_balancer"]["m3u8"]
    except (ValueError, KeyError) as e:
        raise ExtractionError(f"unexpected Rutube response: {e}", SERVICE) from e

    title = data.get("title")
    author = (data.get("author") or {}).get("name")
    thumb = data.get("thumbnail_url") or data.get("picture_url")
    hint = safe_filename(f"rutube_{video_id}_{title or ''}")

    subtitles = []
    for cap in data.get("captions") or []:
        if cap.get("file"):
            subtitles.append(Subtitle(
                lang=cap.get("code") or cap.get("langTitle") or "sub",
                url=cap["file"], ext="vtt"))

    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=m3u8, ext="m3u8")],
        title=title, author=author, source_url=url, filename_hint=hint,
        thumbnail=thumb, subtitles=subtitles)


def extract_timeline(ctx: Context, url: str, limit: int = 30):
    """Rutube channel -> Playlist of its latest videos."""
    from ..models import Playlist
    m = PROFILE_PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("not a Rutube channel URL", SERVICE)
    person_id = m.group(1)
    vids, page = [], 1
    while len(vids) < limit and page <= 5:
        r = ctx.get(f"https://rutube.ru/api/video/person/{person_id}/",
                    params={"page": page})
        if r.status_code != 200:
            break
        results = r.json().get("results") or []
        if not results:
            break
        vids += [v["id"] for v in results if v.get("id")]
        page += 1
    urls = [f"https://rutube.ru/video/{v}/" for v in vids[:limit]]
    entries = gather(lambda u: extract(ctx, u), urls)
    if not entries:
        raise ExtractionError("no videos for this Rutube channel", SERVICE)
    return Playlist(service=SERVICE, entries=entries, author=person_id, source_url=url)
