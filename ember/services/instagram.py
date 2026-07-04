"""Instagram: посты, Reels, карусели.

Самый нестабильный сервис — Instagram агрессивно блокирует анонимный
доступ. Методы, по очереди (как у cobalt):
1. GraphQL-запрос PolarisPostActionLoadPostQueryQuery (без авторизации);
2. embed-страница /p/<код>/embed/captioned/;
3. мобильный oembed API — отдаёт только превью-картинку и метаданные,
   но работает даже там, где первые два метода закрыты.

Полное качество там, где всё закрыто, даёт передача cookies
залогиненного аккаунта: ember.extract(url, cookies={"sessionid": ...}).
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename

SERVICE = "instagram"

PATTERNS = [
    re.compile(r"https?://(?:www\.)?instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)"),
    re.compile(r"https?://(?:www\.)?instagram\.com/share/((?:p|reel|reels)/)?[A-Za-z0-9_-]+"),
]

_IG_APP_ID = "936619743392459"
_GRAPHQL_DOC_ID = "8845758582119845"  # PolarisPostActionLoadPostQueryQuery
_MOBILE_UA = (
    "Instagram 275.0.0.27.98 Android (33/13; 280dpi; 720x1423; "
    "Xiaomi; Redmi 7; onclite; qcom; en_US; 458229258)"
)


def _resolve_shortcode(ctx: Context, url: str) -> str:
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    # share-ссылка — редиректит на обычный пост
    r = ctx.get(url, allow_redirects=True)
    m = re.search(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", r.url)
    if not m:
        raise ExtractionError(
            f"could not determine post shortcode from link {url}", SERVICE)
    return m.group(1)


def _from_graphql(ctx: Context, shortcode: str) -> Optional[dict]:
    headers = {
        "x-ig-app-id": _IG_APP_ID,
        "X-FB-Friendly-Name": "PolarisPostActionLoadPostQueryQuery",
        "content-type": "application/x-www-form-urlencoded",
        "sec-gpc": "1",
        "Referer": f"https://www.instagram.com/p/{shortcode}/",
    }
    payload = {
        "fb_api_req_friendly_name": "PolarisPostActionLoadPostQueryQuery",
        "variables": json.dumps({
            "shortcode": shortcode,
            "fetch_tagged_user_count": None,
            "hoisted_comment_id": None,
            "hoisted_reply_id": None,
        }),
        "server_timestamps": "true",
        "doc_id": _GRAPHQL_DOC_ID,
    }
    r = ctx.post("https://www.instagram.com/graphql/query",
                 headers=headers, data=payload)
    if r.status_code != 200:
        return None
    try:
        return r.json()["data"]["xdt_shortcode_media"]
    except (ValueError, KeyError, TypeError):
        return None


def _media_from_node(node: dict) -> Optional[Media]:
    if node.get("is_video") and node.get("video_url"):
        return Media(kind="video", url=node["video_url"], ext="mp4")
    if node.get("display_url"):
        return Media(kind="photo", url=node["display_url"], ext="jpg")
    return None


def _from_embed(ctx: Context, shortcode: str) -> Optional[dict]:
    """Fallback: парсим embed-страницу. Возвращает минимальный dict в том же формате."""
    r = ctx.get(
        f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
        headers={"Referer": "https://www.instagram.com/"})
    if r.status_code != 200:
        return None
    html = r.text
    # внутри страницы бывает экранированный JSON с shortcode_media
    m = re.search(r'\\"shortcode_media\\":(\{.*?\})\s*\}\s*\]', html)
    if m:
        try:
            unescaped = m.group(1).encode().decode("unicode_escape")
            return json.loads(unescaped)
        except (ValueError, UnicodeDecodeError):
            pass
    # хотя бы video_url напрямую
    m = re.search(r'\\"video_url\\":\\"([^"\\]+)\\"', html)
    if m:
        video_url = m.group(1).encode().decode("unicode_escape")
        return {"is_video": True, "video_url": video_url}
    return None


def _from_oembed(ctx: Context, shortcode: str) -> Optional[dict]:
    """Последний шанс: мобильный oembed. Только превью-картинка + метаданные."""
    r = ctx.get(
        "https://i.instagram.com/api/v1/oembed/",
        params={"url": f"https://www.instagram.com/p/{shortcode}/"},
        headers={"User-Agent": _MOBILE_UA, "x-ig-app-id": _IG_APP_ID})
    if r.status_code != 200:
        return None
    try:
        j = r.json()
    except ValueError:
        return None
    thumb = j.get("thumbnail_url")
    if not thumb:
        return None
    return {
        "display_url": thumb,
        "_thumbnail_only": True,
        "owner": {"username": j.get("author_name")},
        "edge_media_to_caption": {
            "edges": [{"node": {"text": j.get("title") or ""}}]},
    }


def extract(ctx: Context, url: str) -> Result:
    shortcode = _resolve_shortcode(ctx, url)

    data = (_from_graphql(ctx, shortcode)
            or _from_embed(ctx, shortcode)
            or _from_oembed(ctx, shortcode))
    if not data:
        raise ExtractionError(
            "Instagram did not return the post anonymously. Try passing "
            "logged-in account cookies: extract(url, cookies={...}) "
            "or a different IP via proxies={...}", SERVICE)

    owner = data.get("owner") or {}
    author = owner.get("username")
    caption_edges = ((data.get("edge_media_to_caption") or {}).get("edges") or [])
    title = None
    if caption_edges:
        title = ((caption_edges[0].get("node") or {}).get("text") or "").strip() or None

    media_items = []
    sidecar = (data.get("edge_sidecar_to_children") or {}).get("edges") or []
    if sidecar:
        for edge in sidecar:
            item = _media_from_node(edge.get("node") or {})
            if item:
                media_items.append(item)
    else:
        item = _media_from_node(data)
        if item:
            if data.get("_thumbnail_only"):
                item.quality = "thumbnail"
            media_items.append(item)

    if not media_items:
        raise ExtractionError("no video or photo found in the post", SERVICE)

    hint = safe_filename(f"instagram_{author or 'post'}_{shortcode}")
    kind = "single" if len(media_items) == 1 else "gallery"
    return Result(service=SERVICE, kind=kind, media=media_items, title=title,
                  author=author, source_url=url, filename_hint=hint)
