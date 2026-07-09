"""Twitch: clips.

Method — the public GraphQL gql.twitch.tv with a web client-id. Two requests:
1) clip qualities (sourceURL), 2) VideoAccessToken_Clip (signature+token).
Final mp4 = sourceURL?sig=<signature>&token=<value>.
Only clips are supported, not full streams/VODs.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, Result, safe_filename

SERVICE = "twitch"

# публичный web client-id Twitch (как у cobalt и веб-плеера)
_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_GQL = "https://gql.twitch.tv/gql"
_TOKEN_HASH = "36b89d2507fce29e5ca551df756d27c1cfe079e2609642b4390aa4c35796eb11"

PATTERNS = [
    re.compile(r"https?://clips\.twitch\.tv/([\w-]+)"),
    re.compile(r"https?://(?:www\.|m\.)?twitch\.tv/\w+/clip/([\w-]+)"),
    re.compile(r"https?://(?:www\.)?twitch\.tv/clip/([\w-]+)"),
]

PROFILE_PATTERNS = [
    re.compile(r"https?://(?:www\.|m\.)?twitch\.tv/([a-zA-Z0-9_]{2,25})/?$"),
]


def _gql(ctx: Context, payload):
    r = ctx.post(_GQL, headers={"Client-ID": _CLIENT_ID}, json=payload)
    if r.status_code != 200:
        raise ExtractionError(f"Twitch GraphQL returned HTTP {r.status_code}", SERVICE)
    return r.json()


def extract(ctx: Context, url: str) -> Result:
    slug = next((p.match(url).group(1) for p in PATTERNS if p.match(url)), None)
    if not slug:
        raise ExtractionError(
            "only Twitch clips are supported (clips.twitch.tv/... "
            "or twitch.tv/<channel>/clip/...)", SERVICE)

    # 1) метаданные и качества
    info = _gql(ctx, {
        "query": (
            "{ clip(slug: \"%s\") { title durationSeconds thumbnailURL "
            "broadcaster { displayName } videoQualities { quality sourceURL } } }" % slug)
    })
    clip = ((info.get("data") or {}).get("clip")) or {}
    qualities = clip.get("videoQualities") or []
    if not qualities:
        raise ExtractionError("clip not found or has no video streams", SERVICE)

    # 2) токен доступа (подпись)
    token_resp = _gql(ctx, {
        "operationName": "VideoAccessToken_Clip",
        "variables": {"slug": slug},
        "extensions": {"persistedQuery": {
            "version": 1, "sha256Hash": _TOKEN_HASH}},
    })
    access = (((token_resp.get("data") or {}).get("clip")) or {}).get("playbackAccessToken")
    if not access:
        raise ExtractionError("could not obtain the clip access token", SERVICE)

    best = max(qualities, key=lambda q: int(re.sub(r"\D", "", q.get("quality") or "0") or 0))
    sep = "&" if "?" in best["sourceURL"] else "?"
    video_url = (f"{best['sourceURL']}{sep}sig={access['signature']}"
                 f"&token={quote(access['value'])}")

    title = clip.get("title")
    author = (clip.get("broadcaster") or {}).get("displayName")
    hint = safe_filename(f"twitch_{author or ''}_{slug}")

    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=video_url, ext="mp4",
                     quality=best.get("quality"))],
        title=title, author=author, source_url=url, filename_hint=hint,
        thumbnail=clip.get("thumbnailURL"))


def extract_timeline(ctx: Context, url: str, limit: int = 30):
    """Twitch channel -> Playlist of its latest clips."""
    from ..models import Playlist
    m = PROFILE_PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("not a Twitch channel URL", SERVICE)
    login = m.group(1)
    q = ('{ user(login: "%s") { clips(first: %d) { edges { node { slug } } } } }'
         % (login, limit))
    data = _gql(ctx, {"query": q})
    user = (data.get("data") or {}).get("user")
    if not user:
        raise ExtractionError(f"channel {login} not found", SERVICE)
    entries = []
    for edge in (user.get("clips") or {}).get("edges") or []:
        slug = (edge.get("node") or {}).get("slug")
        if not slug:
            continue
        try:
            entries.append(extract(ctx, f"https://clips.twitch.tv/{slug}"))
        except ExtractionError:
            continue
    if not entries:
        raise ExtractionError("no clips for this channel", SERVICE)
    return Playlist(service=SERVICE, entries=entries, author=login, source_url=url)
