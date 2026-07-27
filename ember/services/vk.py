"""VK / VK Video: videos and clips.

Method — the public VK Video mobile API: get an anonymous token, then
video.get. The client constants are the same as cobalt (VK Video iOS app).
Private videos may require account cookies.
"""

from __future__ import annotations

import html
import re
import uuid
from typing import Optional

from .. import cache
from ..errors import ExtractionError
from ..http import DEFAULT_UA, Context
from ..models import Media, MediaVariant, Result, safe_filename

SERVICE = "vk"

_CLIENT_ID = "51552953"
_CLIENT_SECRET = "qgr0yWwXCrsxA1jnRtRX"
_API_VERSION = "5.274"
_UA = ("com.vk.vkvideo.prod/1955 (iPhone, iOS 16.7.15, iPhone10,4, "
       "Scale/2.0) SAK/1.135")

_RESOLUTIONS = ["2160", "1440", "1080", "720", "480", "360", "240", "144"]

PATTERNS = [
    re.compile(r"https?://(?:www\.|m\.)?vk\.com/(?:video|clip)(-?\d+)_(\d+)(?:_(\w+))?"),
    re.compile(r"https?://(?:www\.)?vkvideo\.ru/(?:video|clip)(-?\d+)_(\d+)(?:_(\w+))?"),
    re.compile(r"https?://(?:www\.|m\.)?vk\.com/\w+\?z=(?:video|clip)(-?\d+)_(\d+)(?:%2F|/)?(\w+)?"),
]

PROFILE_PATTERNS = [
    re.compile(r"https?://(?:www\.|m\.)?vk\.com/([\w.]+)/?$"),
    re.compile(r"https?://(?:www\.)?vkvideo\.ru/@?([\w.]+)/?$"),
]


def _parse(url: str):
    for p in PATTERNS:
        m = p.match(url)
        if m:
            return m.group(1), m.group(2), m.group(3)
    # запасной вариант: ищем video<oid>_<id> где угодно
    m = re.search(r"(?:video|clip)(-?\d+)_(\d+)", url)
    if m:
        key = re.search(r"access_key=(\w+)", url)
        return m.group(1), m.group(2), key.group(1) if key else None
    raise ExtractionError("could not parse VK link", SERVICE)


_CACHE_KEY = "vk_anon_token"
_CACHE_TTL = 3600  # токен живёт ограниченно — держим в кэше час


def _get_auth(ctx: Context, headers) -> tuple:
    """Return (token, device_id), caching the pair on disk."""
    cached = cache.get(_CACHE_KEY)
    if cached:
        return cached["token"], cached["device_id"]
    device_id = str(uuid.uuid4()).upper()
    auth = ctx.get("https://api.vk.ru/method/auth.getAnonymToken", headers=headers,
                   params={"client_id": _CLIENT_ID, "client_secret": _CLIENT_SECRET,
                           "device_id": device_id, "v": _API_VERSION})
    try:
        token = auth.json()["response"]["token"]
    except (ValueError, KeyError) as e:
        raise ExtractionError(f"could not obtain VK anonymous token: {e}", SERVICE) from e
    cache.set(_CACHE_KEY, {"token": token, "device_id": device_id}, _CACHE_TTL)
    return token, device_id


def _api(ctx, headers, method: str, params: dict) -> dict:
    """VK API call with the anonymous token, refreshing it if it expired."""
    token, device_id = _get_auth(ctx, headers)

    def call(tok, dev):
        return ctx.post(
            f"https://api.vkvideo.ru/method/{method}",
            headers={**headers,
                     "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            data={"anonymous_token": tok, "device_id": dev, "lang": "en",
                  "v": _API_VERSION, **params}).json()

    data = call(token, device_id)
    if "error" in data:
        cache.invalidate(_CACHE_KEY)
        token, device_id = _get_auth(ctx, headers)
        data = call(token, device_id)
    return data


def _item_to_result(item: dict, url: str = "") -> Result:
    files = item.get("files") or {}
    variants = [MediaVariant(url=files[f"mp4_{res}"], height=int(res),
                             quality=f"{res}p", ext="mp4")
                for res in _RESOLUTIONS if files.get(f"mp4_{res}")]
    if not variants:
        raise ExtractionError(
            "no mp4 streams (possibly a live stream or external video)", SERVICE)
    best = variants[0]
    owner_id, video_id = item.get("owner_id"), item.get("id")
    images = item.get("image") or []
    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=best.url, ext="mp4",
                     quality=best.quality, variants=variants)],
        title=item.get("title"), author=str(owner_id) if owner_id else None,
        source_url=url or f"https://vk.com/video{owner_id}_{video_id}",
        filename_hint=safe_filename(f"vk_{owner_id}_{video_id}_{item.get('title') or ''}"),
        thumbnail=images[-1].get("url") if images else None,
        duration=item.get("duration"), timestamp=item.get("date"),
        view_count=item.get("views"), like_count=(item.get("likes") or {}).get("count"))


_QUALITY_KEY = re.compile(r"^url(\d+)$")


def _walk_params(node):
    """Yield every dict in the al_video.php envelope carrying url<height> keys."""
    if isinstance(node, dict):
        if any(_QUALITY_KEY.match(k) for k in node):
            yield node
        for v in node.values():
            yield from _walk_params(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_params(v)


def has_auth_cookies(ctx: Context) -> bool:
    return any(ck.name.startswith("remixsid") for ck in ctx.session.cookies)


def _from_web(ctx: Context, owner_id: str, video_id: str, url: str) -> Optional[Result]:
    """Authenticated fallback via the web player endpoint.

    The /method/ API only ever sees the anonymous token, so closed groups and
    private videos are invisible to it. al_video.php honours the session
    cookies instead, which is what makes those videos reachable.
    """
    try:
        r = ctx.post("https://vkvideo.ru/al_video.php",
                     params={"act": "show"},
                     headers={"User-Agent": DEFAULT_UA,
                              "X-Requested-With": "XMLHttpRequest",
                              "Referer": "https://vkvideo.ru/"},
                     data={"act": "show", "al": "1",
                           "video": f"{owner_id}_{video_id}"})
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        params = next(_walk_params(r.json()), None)
    except ValueError:
        return None
    if not params:
        return None

    variants = []
    for key, val in params.items():
        m = _QUALITY_KEY.match(key)
        if m and val:
            height = int(m.group(1))
            variants.append(MediaVariant(url=val, height=height,
                                         quality=f"{height}p", ext="mp4"))
    if not variants:
        return None
    variants.sort(key=lambda v: v.height or 0, reverse=True)
    best = variants[0]

    title = params.get("md_title")
    title = html.unescape(title) if title else None
    author = params.get("md_author") or (str(owner_id) if owner_id else None)
    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=best.url, ext="mp4",
                     quality=best.quality, variants=variants,
                     http_headers={"User-Agent": DEFAULT_UA})],
        title=title, author=author, source_url=url,
        filename_hint=safe_filename(f"vk_{owner_id}_{video_id}_{title or ''}"),
        thumbnail=params.get("jpg") or None,
        duration=params.get("duration"), timestamp=params.get("date"),
        view_count=params.get("views"))


def extract(ctx: Context, url: str) -> Result:
    owner_id, video_id, access_key = _parse(url)
    videos = f"{owner_id}_{video_id}" + (f"_{access_key}" if access_key else "")
    data = _api(ctx, {"User-Agent": _UA}, "video.get", {"videos": videos})
    item = None
    try:
        item = data["response"]["items"][0]
    except (LookupError, TypeError):
        pass

    if item:
        try:
            return _item_to_result(item, url)
        except ExtractionError:
            pass          # restricted / stream-less — пробуем плеер с cookies

    web = _from_web(ctx, owner_id, video_id, url)
    if web:
        return web

    if item and item.get("content_restricted"):
        why = item.get("content_restricted_message") or "video unavailable"
        if has_auth_cookies(ctx):
            raise ExtractionError(
                f"VK restricted this video ({why}). The signed-in account has "
                "no access to it — join the group or check who the video is "
                "shared with.", SERVICE)
        raise ExtractionError(
            f"VK restricted this video ({why}). Videos in closed groups need "
            "account cookies — pass --cookies-from-browser (the account must "
            "already have access).", SERVICE)
    if item:
        raise ExtractionError(
            "no mp4 streams (possibly a live stream or external video)", SERVICE)
    raise ExtractionError(
        "VK returned no video (private, deleted, or login required)", SERVICE)


def _resolve_owner(ctx, headers, screen: str):
    if screen.startswith("id") and screen[2:].isdigit():
        return int(screen[2:])
    if screen.startswith(("club", "public", "event")):
        num = re.sub(r"\D", "", screen)
        return -int(num) if num else None
    data = _api(ctx, headers, "utils.resolveScreenName", {"screen_name": screen})
    resp = data.get("response") or {}
    obj = resp.get("object_id")
    if not obj:
        return None
    return obj if resp.get("type") == "user" else -obj


def extract_timeline(ctx: Context, url: str, limit: int = 30):
    """VK profile/community -> Playlist of its latest videos."""
    from ..models import Playlist
    m = next((p.match(url) for p in PROFILE_PATTERNS if p.match(url)), None)
    if not m:
        raise ExtractionError("not a VK profile URL", SERVICE)
    headers = {"User-Agent": _UA}
    owner = _resolve_owner(ctx, headers, m.group(1))
    if owner is None:
        raise ExtractionError(f"could not resolve VK owner '{m.group(1)}'", SERVICE)
    data = _api(ctx, headers, "video.get",
                {"owner_id": str(owner), "count": str(min(limit, 200))})
    items = ((data.get("response") or {}).get("items")) or []
    entries = []
    for item in items:
        try:
            entries.append(_item_to_result(item))
        except ExtractionError:
            continue
    if not entries:
        raise ExtractionError("no videos for this VK owner", SERVICE)
    return Playlist(service=SERVICE, entries=entries, author=str(owner), source_url=url)
