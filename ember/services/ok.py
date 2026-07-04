"""OK.ru (Одноклассники): видео.

Метод — парсинг data-options из HTML страницы видео. Внутри дважды
завёрнутый JSON с массивом mp4-потоков разного качества.
"""

from __future__ import annotations

import json
import re

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, MediaVariant, Result, safe_filename

SERVICE = "ok"

PATTERNS = [
    re.compile(r"https?://(?:www\.|m\.)?ok\.ru/video/(\d+)"),
    re.compile(r"https?://(?:www\.)?odnoklassniki\.ru/video/(\d+)"),
]

# порядок качества как в metadata OK: имя -> высота
_RES_ORDER = ["ultra", "quad", "full", "hd", "sd", "low", "lowest", "mobile"]

_OPTIONS_RE = re.compile(r'data-options="([^"]+)"')


def extract(ctx: Context, url: str) -> Result:
    m = next((p.match(url) for p in PATTERNS if p.match(url)), None)
    if not m:
        raise ExtractionError("could not parse OK.ru link", SERVICE)
    video_id = m.group(1)

    html = ctx.get(f"https://ok.ru/video/{video_id}").text
    opt = _OPTIONS_RE.search(html)
    if not opt:
        raise ExtractionError(
            "page has no data-options (video deleted, private, or login required)",
            SERVICE)
    try:
        raw = opt.group(1).replace("&quot;", '"')
        options = json.loads(raw)
        metadata = json.loads(options["flashvars"]["metadata"])
    except (ValueError, KeyError) as e:
        raise ExtractionError(f"could not parse OK.ru metadata: {e}", SERVICE) from e

    videos = metadata.get("videos") or []
    if not videos:
        raise ExtractionError(
            "no available streams (possibly a live stream or external video)",
            SERVICE)

    # имя качества -> примерная высота (для выбора качества)
    name_to_height = {"mobile": 144, "lowest": 240, "low": 360, "sd": 480,
                      "hd": 720, "full": 1080, "quad": 1440, "ultra": 2160}
    by_name = {v.get("name"): v for v in videos if v.get("url")}
    variants = []
    for name in _RES_ORDER:
        if name in by_name:
            variants.append(MediaVariant(
                url=by_name[name]["url"], height=name_to_height.get(name),
                quality=name, ext="mp4"))
    if not variants:
        variants = [MediaVariant(url=videos[-1]["url"], quality=videos[-1].get("name"))]
    best = variants[0]

    title = (metadata.get("movie") or {}).get("title")
    author = (metadata.get("author") or {}).get("name")
    thumb = (metadata.get("movie") or {}).get("poster")
    hint = safe_filename(f"ok_{video_id}_{title or ''}")

    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=best.url, ext="mp4",
                     quality=best.quality, variants=variants)],
        title=title, author=author, source_url=url, filename_hint=hint,
        thumbnail=thumb)
