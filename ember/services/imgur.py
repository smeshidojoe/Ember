"""Imgur: images, GIFs/videos and albums.

Method — the public post API that the site itself uses. The web client_id is
embedded in Imgur's own JS and works anonymously, so no account is needed.
Albums come back as a media list and become a gallery Result.
"""

from __future__ import annotations

import re

from ..errors import ExtractionError, Reason
from ..http import Context
from ..models import Media, Result, safe_filename, to_timestamp

SERVICE = "imgur"

PATTERNS = [
    re.compile(r"https?://(?:www\.|m\.)?imgur\.com/(?:a|album)/(?:[\w-]+-)?([A-Za-z0-9]+)"),
    re.compile(r"https?://(?:www\.|m\.)?imgur\.com/gallery/(?:[\w-]+-)?([A-Za-z0-9]+)"),
    re.compile(r"https?://(?:www\.|m\.)?imgur\.com/(?:t/[\w-]+/)?([A-Za-z0-9]+)/?$"),
    re.compile(r"https?://i\.imgur\.com/([A-Za-z0-9]+)\.\w+"),
]

# публичный web-client_id Imgur (тот же, что в их собственном фронтенде)
_CLIENT_ID = "546c25a59c58ad7"
_API = "https://api.imgur.com/post/v1"

# в ссылках вида /gallery/some-title-slug-AbC123 идентификатор — последний кусок
_SLUG_TAIL = re.compile(r"([A-Za-z0-9]+)$")


def _ident(url: str) -> str:
    for p in PATTERNS:
        m = p.match(url)
        if m:
            return m.group(1)
    raise ExtractionError("could not parse the Imgur link", SERVICE,
                          reason=Reason.NO_MEDIA)


def _fetch(ctx: Context, ident: str) -> dict:
    """Post metadata. Albums and single media share one endpoint."""
    params = {"client_id": _CLIENT_ID, "include": "media"}
    r = ctx.get(f"{_API}/media/{ident}", params=params)
    if r.status_code == 404:
        # ссылки /a/ и /gallery/ иногда живут только под albums/
        r = ctx.get(f"{_API}/albums/{ident}", params=params)
    if r.status_code == 404:
        raise ExtractionError(
            "Imgur returned 404 — the post was removed or never existed",
            SERVICE, reason=Reason.DELETED)
    if r.status_code != 200:
        raise ExtractionError(
            f"Imgur returned HTTP {r.status_code}", SERVICE)
    try:
        return r.json()
    except ValueError as e:
        raise ExtractionError(f"unexpected Imgur response: {e}", SERVICE,
                              reason=Reason.FORMAT_CHANGED) from e


def _media_from(item: dict) -> Media:
    """One Imgur media entry -> Media. Videos/GIFs carry mp4, stills a picture."""
    url = item.get("url") or ""
    mtype = (item.get("type") or "").lower()
    ext = (url.rsplit(".", 1)[-1].split("?")[0] or "jpg").lower()
    if mtype == "video" or ext in ("mp4", "webm"):
        kind = "video"
    elif ext == "gif":
        kind = "gif"
    else:
        kind = "photo"
    return Media(kind=kind, url=url, ext=ext)


def extract(ctx: Context, url: str) -> Result:
    ident = _ident(url)
    data = _fetch(ctx, ident)

    items = data.get("media") or []
    media = [_media_from(m) for m in items if m.get("url")]
    if not media:
        raise ExtractionError(
            "no image or video in this Imgur post", SERVICE,
            reason=Reason.NO_MEDIA)

    title = (data.get("title") or "").strip() or None
    author = ((data.get("account") or {}).get("username")
              or (data.get("account_url") or None))
    kind = "single" if len(media) == 1 else "gallery"
    hint = safe_filename(f"imgur_{ident}_{title or ''}")
    return Result(
        service=SERVICE, kind=kind, media=media,
        title=title, author=author, source_url=url, filename_hint=hint,
        thumbnail=items[0].get("url") if items else None,
        timestamp=to_timestamp(data.get("created_at")),
        view_count=data.get("view_count"),
        like_count=data.get("upvote_count") or data.get("point_count"))
