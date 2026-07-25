"""Instagram: posts, Reels, carousels.

The flakiest service — Instagram aggressively blocks anonymous access.
Methods, in order (like cobalt):
1. GraphQL query PolarisPostActionLoadPostQueryQuery (no auth);
2. embed page /p/<code>/embed/captioned/;
3. mobile oembed API — returns only a preview image and metadata, but
   works even where the first two are closed.

Where everything is closed, full quality comes from passing logged-in
account cookies: ember.extract(url, cookies={"sessionid": ...}).
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
    re.compile(r"https?://(?:www\.)?instagram\.com/stories/[^/?]+(?:/\d+)?"),
    re.compile(r"https?://(?:www\.)?instagram\.com/share/((?:p|reel|reels)/)?[A-Za-z0-9_-]+"),
]

# /stories/highlights/{id} — коллекция; /stories/{user}/{id} — один элемент;
# /stories/{user} — весь текущий трей историй юзера
_HIGHLIGHT_RE = re.compile(r"instagram\.com/stories/highlights/(\d+)")
_STORY_RE = re.compile(r"instagram\.com/stories/(?!highlights(?:/|$))[^/?]+/(\d+)")
_USER_STORY_RE = re.compile(r"instagram\.com/stories/(?!highlights(?:/|$))([^/?]+)/?(?:\?|$)")

PROFILE_PATTERNS = [
    re.compile(r"https?://(?:www\.)?instagram\.com/(?!p/|reel/|reels/|tv/|share/|explore/)"
               r"([A-Za-z0-9_.]+)/?$"),
]

# highlights берутся по той же ссылке на профиль (см. extract_highlights)
HIGHLIGHTS_PATTERNS = PROFILE_PATTERNS

_PROFILE_ID_RE = re.compile(r'"profilePage_(\d+)"')
_PROFILE_ID_ALT_RE = re.compile(r'"user_id":"(\d+)"')
_HL_QUERY_HASH = "d4d88dc1500312af6f937f7b804c68c3"

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
    """Fallback: parse the embed page. Returns a minimal dict in the same shape."""
    r = ctx.get(
        f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
        headers={"Referer": "https://www.instagram.com/"})
    if r.status_code != 200:
        return None
    # снимаем JS-экранирование кавычек/слэшей; \uXXXX разберёт json.loads
    html = r.text.replace('\\"', '"').replace("\\/", "/")
    m = re.search(r'"shortcode_media":(\{.*?\})\s*\}\s*\]', html)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    m = re.search(r'"video_url":"([^"]+)"', html)
    if m:
        try:
            return {"is_video": True, "video_url": json.loads(f'"{m.group(1)}"')}
        except ValueError:
            return {"is_video": True, "video_url": m.group(1)}
    return None


def _node_from_mobile(m: dict) -> Optional[dict]:
    if m.get("video_versions"):
        return {"is_video": True, "video_url": m["video_versions"][0]["url"]}
    cand = (m.get("image_versions2") or {}).get("candidates") or []
    return {"display_url": cand[0]["url"]} if cand else None


def _shape_item(item: dict) -> Optional[dict]:
    """Mobile media/info item -> GraphQL-shaped dict for _node_to_result()."""
    owner = {"username": (item.get("user") or {}).get("username")}
    caption = {"edges": [{"node": {"text": (item.get("caption") or {}).get("text", "")}}]}
    if item.get("carousel_media"):
        edges = [{"node": n} for m in item["carousel_media"]
                 if (n := _node_from_mobile(m))]
        if not edges:
            return None
        return {"owner": owner, "edge_media_to_caption": caption,
                "edge_sidecar_to_children": {"edges": edges}}
    node = _node_from_mobile(item)
    if not node:
        return None
    node.update({"owner": owner, "edge_media_to_caption": caption})
    return node


def _media_info(ctx: Context, media_id: str) -> Optional[dict]:
    r = ctx.get(f"https://i.instagram.com/api/v1/media/{media_id}/info/",
                headers={"User-Agent": _MOBILE_UA, "x-ig-app-id": _IG_APP_ID})
    if r.status_code != 200:
        return None
    try:
        return _shape_item(r.json()["items"][0])
    except (ValueError, LookupError):
        return None


def _reels_media(ctx: Context, reel_id: str) -> list:
    """feed/reels_media -> list of story items for a reel (highlight or user tray)."""
    r = ctx.get("https://i.instagram.com/api/v1/feed/reels_media/",
                params={"reel_ids": reel_id},
                headers={"User-Agent": _MOBILE_UA, "x-ig-app-id": _IG_APP_ID})
    if r.status_code != 200:
        return []
    try:
        reels = r.json().get("reels") or {}
    except ValueError:
        return []
    reel = reels.get(reel_id) or {}
    return reel.get("items") or []


def _items_gallery(items: list, username: Optional[str] = None) -> Optional[dict]:
    """Story items (highlight/tray) -> one GraphQL-shaped gallery dict."""
    edges = [{"node": n} for it in items if (n := _node_from_mobile(it))]
    if not edges:
        return None
    # имя автора: из элементов, иначе — из ссылки (в трее его часто нет)
    owner = {}
    for it in items:
        name = (it.get("user") or {}).get("username")
        if name:
            owner = {"username": name}
            break
    if not owner.get("username") and username:
        owner = {"username": username}
    return {"owner": owner,
            "edge_media_to_caption": {"edges": [{"node": {"text": ""}}]},
            "edge_sidecar_to_children": {"edges": edges}}


def _user_id(ctx: Context, username: str) -> Optional[str]:
    r = ctx.get("https://i.instagram.com/api/v1/users/web_profile_info/",
                params={"username": username},
                headers={"User-Agent": _MOBILE_UA, "x-ig-app-id": _IG_APP_ID})
    if r.status_code == 200:
        try:
            return r.json()["data"]["user"]["id"]
        except (ValueError, LookupError):
            pass
    # web_profile_info is rate-limited hard (429 on many networks); the
    # profile page itself still carries the id
    r = ctx.get(f"https://www.instagram.com/{username}/",
                headers={"User-Agent": _MOBILE_UA, "x-ig-app-id": _IG_APP_ID})
    if r.status_code != 200:
        return None
    m = _PROFILE_ID_RE.search(r.text) or _PROFILE_ID_ALT_RE.search(r.text)
    return m.group(1) if m else None


def _highlight_reels(ctx: Context, user_id: str) -> list:
    """List a profile's highlight collections -> [{id, title}, ...]."""
    r = ctx.get("https://www.instagram.com/graphql/query/",
                headers={"x-ig-app-id": _IG_APP_ID,
                         "Referer": "https://www.instagram.com/"},
                params={"query_hash": _HL_QUERY_HASH,
                        "variables": json.dumps({
                            "user_id": user_id,
                            "include_chaining": False,
                            "include_reel": True,
                            "include_suggested_users": False,
                            "include_logged_out_extras": False,
                            "include_highlight_reels": True,
                            "include_live_status": False})})
    if r.status_code != 200:
        return []
    try:
        edges = r.json()["data"]["user"]["edge_highlight_reels"]["edges"]
    except (ValueError, LookupError):
        return []
    return [e["node"] for e in edges if (e.get("node") or {}).get("id")]


def _from_mobile_info(ctx: Context, shortcode: str) -> Optional[dict]:
    """Mobile media/info — carries carousel_media (full carousel). Needs a
    non-blocked IP or cookies; returns a GraphQL-shaped dict for extract()."""
    r = ctx.get("https://i.instagram.com/api/v1/oembed/",
                params={"url": f"https://www.instagram.com/p/{shortcode}/"},
                headers={"User-Agent": _MOBILE_UA, "x-ig-app-id": _IG_APP_ID})
    media_id = r.json().get("media_id") if r.status_code == 200 else None
    return _media_info(ctx, media_id) if media_id else None


def _from_oembed(ctx: Context, shortcode: str) -> Optional[dict]:
    """Last resort: mobile oembed. Preview image + metadata only."""
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


def _node_to_result(data: dict, url: str, shortcode: str = "") -> Optional[Result]:
    """Build a Result from a shortcode_media / timeline node."""
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
        return None

    sc = shortcode or data.get("shortcode") or ""
    hint = safe_filename(f"instagram_{author or 'post'}_{sc}")
    kind = "single" if len(media_items) == 1 else "gallery"
    likes = data.get("like_count")
    if likes is None:
        likes = (data.get("edge_media_preview_like") or {}).get("count")
    return Result(service=SERVICE, kind=kind, media=media_items, title=title,
                  author=author, source_url=url, filename_hint=hint,
                  duration=data.get("video_duration"),
                  timestamp=data.get("taken_at") or data.get("taken_at_timestamp"),
                  view_count=data.get("view_count") or data.get("play_count"),
                  like_count=likes)


_STORY_HELP = ("Stories require logged-in account cookies "
               "(and they expire after 24h).")


def _story_result(ctx: Context, url: str, data: Optional[dict], what: str) -> Result:
    if not data:
        raise ExtractionError(
            f"Instagram did not return the {what}. {_STORY_HELP}", SERVICE)
    res = _node_to_result(data, url)
    if res is None:
        raise ExtractionError(f"no video or photo found in the {what}", SERVICE)
    return res


def extract(ctx: Context, url: str) -> Result:
    hl = _HIGHLIGHT_RE.search(url)
    if hl:
        items = _reels_media(ctx, f"highlight:{hl.group(1)}")
        return _story_result(ctx, url, _items_gallery(items), "highlight")
    story = _STORY_RE.search(url)
    if story:
        return _story_result(ctx, url, _media_info(ctx, story.group(1)), "story")
    tray = _USER_STORY_RE.search(url)
    if tray:
        username = tray.group(1)
        uid = _user_id(ctx, username)
        items = _reels_media(ctx, uid) if uid else []
        return _story_result(ctx, url, _items_gallery(items, username),
                             "user's stories")
    shortcode = _resolve_shortcode(ctx, url)
    data = (_from_graphql(ctx, shortcode)
            or _from_mobile_info(ctx, shortcode)
            or _from_embed(ctx, shortcode)
            or _from_oembed(ctx, shortcode))
    if not data:
        raise ExtractionError(
            "Instagram did not return the post anonymously. It needs "
            "logged-in account cookies, or a different IP (proxy).", SERVICE)
    res = _node_to_result(data, url, shortcode)
    if res is None:
        raise ExtractionError("no video or photo found in the post", SERVICE)
    return res


def extract_highlights(ctx: Context, url: str, limit: int = 30):
    """Instagram profile -> Playlist of its highlight collections.

    One entry per highlight (the round covers pinned above the posts); each
    entry is a gallery of that collection's stories. Needs account cookies."""
    from ..http import gather
    from ..models import Playlist
    m = HIGHLIGHTS_PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("not an Instagram profile URL", SERVICE)
    username = m.group(1)

    user_id = _user_id(ctx, username)
    if not user_id:
        raise ExtractionError(
            f"could not resolve the Instagram user id for '{username}' — "
            "needs account cookies or a different IP (proxy)", SERVICE)
    nodes = _highlight_reels(ctx, user_id)
    if not nodes:
        raise ExtractionError(
            f"no highlights for '{username}' (or Instagram hid them). "
            + _STORY_HELP, SERVICE)

    def one(node):
        data = _items_gallery(_reels_media(ctx, f"highlight:{node['id']}"), username)
        if not data:
            return None
        res = _node_to_result(data, url)
        if res is None:
            return None
        title = (node.get("title") or "").strip() or None
        res.title = title
        res.filename_hint = safe_filename(
            f"instagram_{username}_highlight_{title or node['id']}")
        return res

    entries = gather(one, nodes[:limit])
    if not entries:
        raise ExtractionError(
            f"highlights of '{username}' returned no media. " + _STORY_HELP, SERVICE)
    return Playlist(service=SERVICE, entries=entries, author=username,
                    title=f"{username} highlights", source_url=url)


def extract_timeline(ctx: Context, url: str, limit: int = 30):
    """Instagram profile -> Playlist of the latest posts.

    Uses web_profile_info; needs account cookies or a non-blocked IP
    (same wall as post extraction on restricted networks)."""
    from ..models import Playlist
    m = PROFILE_PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("not an Instagram profile URL", SERVICE)
    username = m.group(1)
    r = ctx.get("https://i.instagram.com/api/v1/users/web_profile_info/",
                params={"username": username},
                headers={"x-ig-app-id": _IG_APP_ID, "User-Agent": _MOBILE_UA})
    if r.status_code != 200:
        raise ExtractionError(
            f"Instagram returned HTTP {r.status_code} for the profile — "
            "needs account cookies or a different IP (proxy)", SERVICE)
    try:
        edges = r.json()["data"]["user"]["edge_owner_to_timeline_media"]["edges"]
    except (ValueError, LookupError) as e:
        raise ExtractionError(f"unexpected Instagram response: {e}", SERVICE) from e
    entries = []
    for edge in edges[:limit]:
        node = edge.get("node") or {}
        res = _node_to_result(node, f"https://www.instagram.com/p/{node.get('shortcode','')}/")
        if res is not None:
            entries.append(res)
    if not entries:
        raise ExtractionError("no posts with media for this profile", SERVICE)
    return Playlist(service=SERVICE, entries=entries, author=username, source_url=url)
