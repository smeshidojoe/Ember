"""SoundCloud: аудио (треки).

client_id публично не выдаётся, поэтому вытаскиваем его из JS сайта
(как cobalt) и кэшируем на процесс. Затем resolve → выбираем
прогрессивный поток (обычно mp3) → получаем финальный URL файла.
"""

from __future__ import annotations

import re

from .. import cache
from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename

SERVICE = "soundcloud"

PATTERNS = [
    re.compile(r"https?://(?:www\.|m\.)?soundcloud\.com/[\w-]+/sets/[\w-]+/?(?:\?.*)?$"),
    re.compile(r"https?://(?:www\.|m\.)?soundcloud\.com/[\w-]+/[\w-]+/?(?:\?.*)?$"),
    re.compile(r"https?://on\.soundcloud\.com/[\w-]+"),
]

_CACHE_KEY = "soundcloud_client_id"
_CACHE_TTL = 7 * 24 * 3600  # client_id живёт долго — кэшируем на неделю
_SCRIPT_RE = re.compile(r'<script[^>]+src="(https://a-v2\.sndcdn\.com/[^"]+)"')
_CID_INLINE_RE = re.compile(r'client_id:"([A-Za-z0-9]{32})"')


def _scrape_client_id(ctx: Context) -> str:
    home = ctx.get("https://soundcloud.com/").text
    for script_url in _SCRIPT_RE.findall(home):
        js = ctx.get(script_url).text
        m = _CID_INLINE_RE.search(js)
        if m:
            return m.group(1)
    raise ExtractionError("could not obtain SoundCloud client_id", SERVICE)


def _get_client_id(ctx: Context, force_refresh: bool = False) -> str:
    if force_refresh:
        cache.invalidate(_CACHE_KEY)
    return cache.get_or_set(_CACHE_KEY, _CACHE_TTL, lambda: _scrape_client_id(ctx))


def _resolve(ctx: Context, url: str) -> dict:
    client_id = _get_client_id(ctx)
    r = ctx.get("https://api-v2.soundcloud.com/resolve",
                params={"url": url, "client_id": client_id})
    if r.status_code in (401, 403):  # закэшированный client_id протух
        _get_client_id(ctx, force_refresh=True)
        client_id = _get_client_id(ctx)
        r = ctx.get("https://api-v2.soundcloud.com/resolve",
                    params={"url": url, "client_id": client_id})
    if r.status_code != 200:
        raise ExtractionError(
            f"SoundCloud resolve returned HTTP {r.status_code} "
            "(private, deleted or unavailable)", SERVICE)
    return r.json()


def _track_result(ctx: Context, track: dict, url: str = "") -> Result:
    """Строит Result из данных трека (докачивая полную инфу при нужде)."""
    if "media" not in track and track.get("id"):
        client_id = _get_client_id(ctx)
        track = ctx.get(f"https://api-v2.soundcloud.com/tracks/{track['id']}",
                        params={"client_id": client_id}).json()

    transcodings = ((track.get("media") or {}).get("transcodings")) or []
    if not transcodings:
        raise ExtractionError("track has no available streams (Go+ or geo-block)", SERVICE)
    chosen = next((t for t in transcodings
                   if (t.get("format") or {}).get("protocol") == "progressive"),
                  transcodings[0])
    is_hls = (chosen.get("format") or {}).get("protocol") == "hls"

    client_id = _get_client_id(ctx)
    stream = ctx.get(chosen["url"], params={
        "client_id": client_id,
        "track_authorization": track.get("track_authorization", ""),
    })
    if stream.status_code != 200:
        raise ExtractionError(
            f"could not get the stream (HTTP {stream.status_code})", SERVICE)
    file_url = stream.json().get("url")
    if not file_url:
        raise ExtractionError("server did not return a file URL", SERVICE)

    title = track.get("title")
    author = (track.get("user") or {}).get("username")
    hint = safe_filename(f"soundcloud_{author or ''}_{title or track.get('id')}")
    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="audio", url=file_url, ext="m3u8" if is_hls else "mp3")],
        title=title, author=author, source_url=url or track.get("permalink_url", ""),
        filename_hint=hint, thumbnail=track.get("artwork_url"))


def extract(ctx: Context, url: str) -> Result:
    if "on.soundcloud.com" in url:
        url = ctx.get(url, allow_redirects=True).url
    data = _resolve(ctx, url)
    if data.get("kind") == "playlist":
        raise ExtractionError(
            "this is a set (playlist) — use extract_playlist()", SERVICE)
    if data.get("kind") != "track":
        raise ExtractionError("the link is not a single track", SERVICE)
    return _track_result(ctx, data, url)


def extract_playlist(ctx: Context, url: str):
    """Плейлист (set) SoundCloud -> Playlist со списком Result по трекам."""
    from ..models import Playlist
    if "on.soundcloud.com" in url:
        url = ctx.get(url, allow_redirects=True).url
    data = _resolve(ctx, url)
    if data.get("kind") == "track":
        return Playlist(service=SERVICE, entries=[_track_result(ctx, data, url)],
                        title=data.get("title"), source_url=url)
    if data.get("kind") != "playlist":
        raise ExtractionError("the link is not a SoundCloud set", SERVICE)

    entries = []
    for track in data.get("tracks") or []:
        try:
            entries.append(_track_result(ctx, track))
        except ExtractionError:
            continue  # пропускаем недоступные треки, не роняя весь набор
    if not entries:
        raise ExtractionError("the set has no available tracks", SERVICE)
    return Playlist(service=SERVICE, entries=entries, title=data.get("title"),
                    author=(data.get("user") or {}).get("username"), source_url=url)
