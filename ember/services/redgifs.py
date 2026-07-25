"""RedGifs: short videos (gifs).

Method — the public v2 API. A temporary bearer token is issued by
/v2/auth/temporary (no account needed) and cached; /v2/gifs/<id> then
returns direct mp4 URLs in HD and SD.
"""

from __future__ import annotations

import re

from .. import cache
from ..errors import ExtractionError
from ..http import Context
from ..models import Media, MediaVariant, Result, safe_filename

SERVICE = "redgifs"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?redgifs\.com/(?:watch|ifr)/([A-Za-z0-9]+)"),
    re.compile(r"https?://(?:thumbs\d*\.|media\.)?redgifs\.com/([A-Za-z0-9]+)\.mp4"),
]

_API = "https://api.redgifs.com/v2"
_CACHE_KEY = "redgifs_token"
_CACHE_TTL = 3600  # временный токен живёт недолго


def _token(ctx: Context, force_refresh: bool = False) -> str:
    if force_refresh:
        cache.invalidate(_CACHE_KEY)

    def fetch() -> str:
        r = ctx.get(f"{_API}/auth/temporary")
        try:
            token = r.json()["token"]
        except (ValueError, KeyError) as e:
            raise ExtractionError(
                f"could not obtain a RedGifs token: {e}", SERVICE) from e
        return token

    return cache.get_or_set(_CACHE_KEY, _CACHE_TTL, fetch)


def _fetch_gif(ctx: Context, gif_id: str) -> dict:
    """GET the gif, refreshing the cached token once if it expired."""
    def call(tok):
        return ctx.get(f"{_API}/gifs/{gif_id}",
                       headers={"Authorization": f"Bearer {tok}"})

    r = call(_token(ctx))
    if r.status_code in (401, 403):
        r = call(_token(ctx, force_refresh=True))
    if r.status_code == 404:
        raise ExtractionError("gif not found or deleted", SERVICE)
    if r.status_code != 200:
        raise ExtractionError(f"RedGifs returned HTTP {r.status_code}", SERVICE)
    try:
        return r.json()["gif"]
    except (ValueError, KeyError) as e:
        raise ExtractionError(f"unexpected RedGifs response: {e}", SERVICE) from e


def extract(ctx: Context, url: str) -> Result:
    gif_id = next((p.match(url).group(1) for p in PATTERNS if p.match(url)), None)
    if not gif_id:
        raise ExtractionError("could not parse RedGifs link", SERVICE)
    # id в ссылках регистронезависим, API ждёт нижний регистр
    gif = _fetch_gif(ctx, gif_id.lower())

    urls = gif.get("urls") or {}
    height = gif.get("height")
    variants = []
    if urls.get("hd"):
        variants.append(MediaVariant(url=urls["hd"], height=height,
                                     quality="hd", ext="mp4"))
    if urls.get("sd"):
        variants.append(MediaVariant(url=urls["sd"], height=480,
                                     quality="sd", ext="mp4"))
    if not variants:
        raise ExtractionError("gif has no mp4 stream", SERVICE)
    best = variants[0]

    author = gif.get("userName")
    tags = gif.get("tags") or []
    title = gif.get("description") or (", ".join(tags[:4]) or None)
    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=best.url, ext="mp4",
                     quality=best.quality, variants=variants)],
        title=title, author=author, source_url=url,
        filename_hint=safe_filename(f"redgifs_{author or ''}_{gif_id}"),
        thumbnail=urls.get("poster") or urls.get("thumbnail"),
        duration=gif.get("duration"), timestamp=gif.get("createDate"),
        view_count=gif.get("views"), like_count=gif.get("likes"))
