"""VK / VK Видео: видео и клипы.

Метод — публичное мобильное API VK Видео: получаем анонимный токен,
затем video.get. Константы клиента — те же, что у cobalt (приложение
VK Видео для iOS). Приватные видео могут требовать cookies аккаунта.
"""

from __future__ import annotations

import re
import uuid

from .. import cache
from ..errors import ExtractionError
from ..http import Context
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
    """Возвращает (token, device_id), кэшируя пару на диск."""
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


def extract(ctx: Context, url: str) -> Result:
    owner_id, video_id, access_key = _parse(url)
    headers = {"User-Agent": _UA}
    token, device_id = _get_auth(ctx, headers)

    videos = f"{owner_id}_{video_id}" + (f"_{access_key}" if access_key else "")

    def video_get(tok, dev):
        return ctx.post(
            "https://api.vkvideo.ru/method/video.get",
            headers={**headers,
                     "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            data={"anonymous_token": tok, "device_id": dev, "lang": "en",
                  "v": _API_VERSION, "videos": videos}).json()

    data = video_get(token, device_id)
    # закэшированный токен мог протухнуть (ошибка авторизации) -> обновляем
    if "error" in data:
        cache.invalidate(_CACHE_KEY)
        token, device_id = _get_auth(ctx, headers)
        data = video_get(token, device_id)

    try:
        item = data["response"]["items"][0]
    except (LookupError, TypeError) as e:
        raise ExtractionError(
            f"VK returned no video: {e} (private, deleted, or login required)", SERVICE) from e

    files = item.get("files") or {}
    variants = []
    for res in _RESOLUTIONS:
        u = files.get(f"mp4_{res}")
        if u:
            variants.append(MediaVariant(url=u, height=int(res),
                                         quality=f"{res}p", ext="mp4"))
    if not variants:
        raise ExtractionError(
            "no mp4 streams (possibly a live stream or external video)", SERVICE)
    best = variants[0]  # _RESOLUTIONS отсортирован по убыванию

    title = item.get("title")
    author_id = item.get("owner_id")
    thumb = None
    images = item.get("image") or []
    if images:
        thumb = images[-1].get("url")  # последний — самый крупный
    hint = safe_filename(f"vk_{owner_id}_{video_id}_{title or ''}")

    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=best.url, ext="mp4",
                     quality=best.quality, variants=variants)],
        title=title, author=str(author_id) if author_id else None,
        source_url=url, filename_hint=hint, thumbnail=thumb)
