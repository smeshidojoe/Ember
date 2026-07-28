"""Twitch: clips and VODs (past broadcasts).

Method — the public GraphQL gql.twitch.tv with a web client-id.
Clips: 1) clip qualities (sourceURL), 2) VideoAccessToken_Clip
(signature+token); final mp4 = sourceURL?sig=<signature>&token=<value>.
VODs: videoPlaybackAccessToken -> usher.ttvnw.net master m3u8 (HLS).
Live streams are not supported — only finished recordings.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..errors import ExtractionError, Reason
from ..http import Context, gather
from ..models import Media, Result, safe_filename, to_timestamp

SERVICE = "twitch"

# публичный web client-id Twitch (как у cobalt и веб-плеера)
_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_GQL = "https://gql.twitch.tv/gql"
_USHER = "https://usher.ttvnw.net/vod/{}.m3u8"

PATTERNS = [
    re.compile(r"https?://clips\.twitch\.tv/([\w-]+)"),
    re.compile(r"https?://(?:www\.|m\.)?twitch\.tv/\w+/clip/([\w-]+)"),
    re.compile(r"https?://(?:www\.)?twitch\.tv/clip/([\w-]+)"),
    re.compile(r"https?://(?:www\.|m\.)?twitch\.tv/videos/(\d+)"),
]

_VOD_RE = re.compile(r"https?://(?:www\.|m\.)?twitch\.tv/videos/(\d+)")

PROFILE_PATTERNS = [
    re.compile(r"https?://(?:www\.|m\.)?twitch\.tv/([a-zA-Z0-9_]{2,25})/?$"),
]


def _gql(ctx: Context, payload):
    r = ctx.post(_GQL, headers={"Client-ID": _CLIENT_ID}, json=payload)
    if r.status_code != 200:
        raise ExtractionError(f"Twitch GraphQL returned HTTP {r.status_code}", SERVICE)
    return r.json()


def _extract_vod(ctx: Context, url: str, vod_id: str) -> Result:
    """Past broadcast -> HLS master playlist signed with a playback token."""
    info = _gql(ctx, {"query":
        '{ video(id: "%s") { title lengthSeconds previewThumbnailURL '
        'viewCount createdAt owner { displayName } } }' % vod_id})
    video = ((info.get("data") or {}).get("video")) or {}
    if not video:
        raise ExtractionError(
            "VOD not found — it was deleted, is subscriber-only, or expired",
            SERVICE, reason=Reason.DELETED)

    token = _gql(ctx, {
        "query": ("query($id: ID!) { videoPlaybackAccessToken(id: $id, params: "
                  '{platform:"web", playerBackend:"mediaplayer", '
                  'playerType:"site"}) { value signature } }'),
        "variables": {"id": vod_id}})
    access = ((token.get("data") or {}).get("videoPlaybackAccessToken")) or {}
    if not access.get("value"):
        raise ExtractionError(
            "could not obtain the VOD access token (subscriber-only VODs need "
            "account cookies)", SERVICE, reason=Reason.NEEDS_AUTH)

    master = _USHER.format(vod_id)
    sep = "?"
    playlist = (f"{master}{sep}token={quote(access['value'])}"
                f"&sig={access['signature']}&allow_source=true&player=twitchweb")

    title = video.get("title")
    author = (video.get("owner") or {}).get("displayName")
    hint = safe_filename(f"twitch_{author or ''}_{vod_id}_{title or ''}")
    return Result(
        service=SERVICE, kind="single",
        media=[Media(kind="video", url=playlist, ext="m3u8")],
        title=title, author=author, source_url=url, filename_hint=hint,
        thumbnail=video.get("previewThumbnailURL"),
        duration=video.get("lengthSeconds"),
        timestamp=to_timestamp(video.get("createdAt")),
        view_count=video.get("viewCount"))


def extract(ctx: Context, url: str) -> Result:
    vod = _VOD_RE.match(url)
    if vod:
        return _extract_vod(ctx, url, vod.group(1))

    slug = next((p.match(url).group(1) for p in PATTERNS if p.match(url)), None)
    if not slug:
        raise ExtractionError(
            "unsupported Twitch link — use a clip (clips.twitch.tv/... or "
            "twitch.tv/<channel>/clip/...) or a VOD (twitch.tv/videos/<id>)",
            SERVICE, reason=Reason.NO_MEDIA)

    # 1) метаданные и качества
    info = _gql(ctx, {
        "query": (
            "{ clip(slug: \"%s\") { title durationSeconds thumbnailURL viewCount "
            "createdAt broadcaster { displayName } "
            "videoQualities { quality sourceURL } } }" % slug)
    })
    clip = ((info.get("data") or {}).get("clip")) or {}
    qualities = clip.get("videoQualities") or []
    if not qualities:
        raise ExtractionError("clip not found or has no video streams", SERVICE)

    # 2) токен доступа (подпись). Сырой запрос, а не persistedQuery: хеши
    # у Twitch протухают, и тогда VideoAccessToken_Clip отдаёт
    # PersistedQueryNotFound, ломая все клипы разом.
    token_resp = _gql(ctx, {
        "query": ("query($slug: ID!) { clip(slug: $slug) { playbackAccessToken("
                  'params: {platform:"web", playerBackend:"mediaplayer", '
                  'playerType:"site"}) { value signature } } }'),
        "variables": {"slug": slug},
    })
    access = (((token_resp.get("data") or {}).get("clip")) or {}).get("playbackAccessToken")
    if not access or not access.get("value"):
        raise ExtractionError("could not obtain the clip access token", SERVICE,
                              reason=Reason.RESTRICTED)

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
        thumbnail=clip.get("thumbnailURL"), duration=clip.get("durationSeconds"),
        timestamp=to_timestamp(clip.get("createdAt")), view_count=clip.get("viewCount"))


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
    urls = [f"https://clips.twitch.tv/{e['node']['slug']}"
            for e in (user.get("clips") or {}).get("edges") or []
            if (e.get("node") or {}).get("slug")]
    entries = gather(lambda u: extract(ctx, u), urls)
    if not entries:
        raise ExtractionError("no clips for this channel", SERVICE)
    return Playlist(service=SERVICE, entries=entries, author=login, source_url=url)
