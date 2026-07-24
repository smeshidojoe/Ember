"""Pornhub: videos.

Method — the video page embeds `var flashvars_<id> = {...}` with
mediaDefinitions (per-quality HLS .m3u8) plus title/duration/thumbnail.
An age cookie is set automatically so anonymous access works.
"""

from __future__ import annotations

import json
import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, MediaVariant, Result, safe_filename

SERVICE = "pornhub"

# любой хост, содержащий pornhub (зеркала/premium/поддомены/TLD)
PATTERNS = [
    re.compile(r"https?://[^/]*pornhub[^/]*/view_video\.php\?viewkey=([\w-]+)"),
    re.compile(r"https?://[^/]*pornhub[^/]*/embed/([\w-]+)"),
]

_FLASHVARS_RE = re.compile(r"var\s+flashvars_\d+\s*=\s*(\{.*?\});", re.DOTALL)


def extract(ctx: Context, url: str) -> Result:
    viewkey = next((p.match(url).group(1) for p in PATTERNS if p.match(url)), None)
    if not viewkey:
        raise ExtractionError("could not parse Pornhub link", SERVICE)

    # проходим возрастной баннер без логина
    ctx.session.cookies.set("age_verified", "1", domain=".pornhub.com")
    ctx.session.cookies.set("accessAgeDisclaimerPH", "1", domain=".pornhub.com")

    r = ctx.get(f"https://www.pornhub.com/view_video.php?viewkey={viewkey}")
    if r.status_code != 200:
        raise ExtractionError(
            f"Pornhub returned HTTP {r.status_code} (removed, private, or geo-blocked)",
            SERVICE)
    m = _FLASHVARS_RE.search(r.text)
    if not m:
        raise ExtractionError(
            "no flashvars on the page (age wall, removed, or members-only)", SERVICE)
    try:
        fv = json.loads(m.group(1))
    except ValueError as e:
        raise ExtractionError(f"could not parse Pornhub flashvars: {e}", SERVICE) from e

    variants = []
    for d in fv.get("mediaDefinitions") or []:
        q, u = d.get("quality"), d.get("videoUrl")
        if d.get("format") == "hls" and u and str(q).isdigit():
            variants.append(MediaVariant(url=u, height=int(q),
                                         quality=f"{q}p", ext="m3u8"))
    if not variants:
        raise ExtractionError(
            "no HLS streams found (some videos need a logged-in account)", SERVICE)
    variants.sort(key=lambda v: v.height or 0, reverse=True)
    best = variants[0]

    # phncdn отдаёт сегменты только с Referer
    headers = {"Referer": "https://www.pornhub.com/"}
    title = fv.get("video_title")
    hint = safe_filename(f"pornhub_{viewkey}_{title or ''}")
    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=best.url, ext="m3u8", quality=best.quality,
                     http_headers=headers, variants=variants)],
        title=title, source_url=url, filename_hint=hint,
        thumbnail=fv.get("image_url"), duration=fv.get("video_duration"))
