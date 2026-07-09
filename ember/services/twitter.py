"""Twitter/X: video, GIFs and photos from tweets.

Two methods, in order:
1. Syndication API (cdn.syndication.twimg.com) — no auth, the token is
   computed from the tweet id. Fast and stable.
2. GraphQL TweetResultByRestId with a guest token — like cobalt, works
   when syndication doesn't return the tweet.
"""

from __future__ import annotations

import json
import math
import re
from typing import Optional

from ..errors import ExtractionError
from ..http import Context
from ..models import Media, MediaVariant, Result, safe_filename

SERVICE = "twitter"

PATTERNS = [
    re.compile(r"https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/[^/]+/status(?:es)?/(\d+)"),
]

PROFILE_PATTERNS = [
    re.compile(r"https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/(?!i/|home$|search)"
               r"([A-Za-z0-9_]{1,15})/?$"),
]

# публичный веб-bearer, тот же что в cobalt и веб-клиенте твиттера
_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

_GRAPHQL_URL = "https://api.x.com/graphql/0hWvDhmW8YQ-S_ib3azIrw/TweetResultByRestId"

_GRAPHQL_FEATURES = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


def _syndication_token(tweet_id: str) -> str:
    """Port of the JS expression ((id/1e15)*PI).toString(36).replace(/(0+|\\.)/g,'')."""
    value = (int(tweet_id) / 1e15) * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    integer = int(value)
    frac = value - integer
    out = ""
    while integer > 0:
        integer, rem = divmod(integer, 36)
        out = digits[rem] + out
    for _ in range(12):
        frac *= 36
        d = int(frac)
        out += digits[d]
        frac -= d
        if frac <= 0:
            break
    return re.sub(r"0+|\.", "", out)


def _from_syndication(ctx: Context, tweet_id: str):
    r = ctx.get(
        "https://cdn.syndication.twimg.com/tweet-result",
        params={"id": tweet_id, "token": _syndication_token(tweet_id)},
    )
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if data.get("__typename") == "TweetTombstone" or not data.get("id_str"):
        return None
    return data


def _cookie_value(ctx: Context, name: str):
    for c in ctx.session.cookies:
        if c.name == name:
            return c.value
    return None


def has_auth_cookies(ctx: Context) -> bool:
    """True if logged-in X account cookies were provided."""
    return bool(_cookie_value(ctx, "auth_token") and _cookie_value(ctx, "ct0"))


def _from_graphql(ctx: Context, tweet_id: str):
    headers = {
        "authorization": _BEARER,
        "x-twitter-client-language": "en",
        "x-twitter-active-user": "yes",
        "content-type": "application/json",
    }
    if has_auth_cookies(ctx):
        # авторизованный запрос: cookies уйдут из сессии автоматически,
        # нужен только csrf-заголовок, равный cookie ct0
        headers["x-csrf-token"] = _cookie_value(ctx, "ct0")
        headers["x-twitter-auth-type"] = "OAuth2Session"
    else:
        act = ctx.post("https://api.x.com/1.1/guest/activate.json", headers=headers)
        if act.status_code != 200:
            return None
        guest_token = act.json().get("guest_token")
        if not guest_token:
            return None
        headers["x-guest-token"] = guest_token

    params = {
        "variables": json.dumps({
            "tweetId": tweet_id,
            "withCommunity": False,
            "includePromotedContent": False,
            "withVoice": False,
        }),
        "features": json.dumps(_GRAPHQL_FEATURES),
    }
    r = ctx.get(_GRAPHQL_URL, headers=headers, params=params)
    if r.status_code != 200:
        return None
    try:
        result = r.json()["data"]["tweetResult"]["result"]
    except (ValueError, KeyError):
        return None
    if result.get("__typename") == "TweetUnavailable":
        return None
    if "tweet" in result:  # TweetWithVisibilityResults
        result = result["tweet"]
    return result


def _append_entry(entry: dict, media_items: list, thumbs: list) -> None:
    """Append media from mediaDetails/extended_entities with quality variants."""
    etype = entry.get("type")
    if etype in ("video", "animated_gif"):
        info = entry.get("video_info") or {}
        mp4 = [v for v in info.get("variants", [])
               if (v.get("content_type") or v.get("type")) == "video/mp4"]
        if not mp4:
            return
        variants = []
        for v in sorted(mp4, key=lambda x: int(x.get("bitrate") or 0), reverse=True):
            u = v.get("url") or v.get("src")
            hm = re.search(r"/(\d+)x(\d+)/", u or "")
            height = int(hm.group(2)) if hm else None
            variants.append(MediaVariant(url=u, height=height,
                                         quality=f"{height}p" if height else None,
                                         ext="mp4"))
        kind = "gif" if etype == "animated_gif" else "video"
        media_items.append(Media(kind=kind, url=variants[0].url, ext="mp4",
                                 quality=variants[0].quality, variants=variants))
        if entry.get("media_url_https"):
            thumbs.append(entry["media_url_https"])
    elif etype == "photo" and entry.get("media_url_https"):
        media_items.append(Media(kind="photo",
                                 url=entry["media_url_https"] + "?name=orig",
                                 ext="jpg"))


def extract(ctx: Context, url: str) -> Result:
    m = re.search(r"/status(?:es)?/(\d+)", url)
    if not m:
        raise ExtractionError("no tweet id in the link", SERVICE)
    tweet_id = m.group(1)

    media_items, thumbs, title, author = [], [], None, None

    # с cookies аккаунта GraphQL видит и NSFW-твиты — syndication нет,
    # поэтому при наличии авторизации идём сразу в GraphQL
    data = None if has_auth_cookies(ctx) else _from_syndication(ctx, tweet_id)
    if data:
        title = (data.get("text") or "").strip() or None
        author = (data.get("user") or {}).get("screen_name")
        for entry in data.get("mediaDetails") or []:
            _append_entry(entry, media_items, thumbs)

    if not media_items:
        result = _from_graphql(ctx, tweet_id)
        if result:
            legacy = result.get("legacy") or {}
            title = (legacy.get("full_text") or "").strip() or title
            core_user = (((result.get("core") or {}).get("user_results") or {})
                         .get("result") or {})
            author = ((core_user.get("legacy") or {}).get("screen_name")
                      or (core_user.get("core") or {}).get("screen_name")
                      or author)
            entities = (legacy.get("extended_entities") or {}).get("media") or []
            for entry in entities:
                _append_entry(entry, media_items, thumbs)

    if not media_items:
        if has_auth_cookies(ctx):
            raise ExtractionError(
                "could not get media even with cookies: tweet deleted, "
                "private, or has no video/photo", SERVICE)
        raise ExtractionError(
            "could not get media: tweet deleted, private, or NSFW. "
            "NSFW tweets need account cookies (auth_token and ct0).",
            SERVICE)

    hint = safe_filename(f"twitter_{author or 'tweet'}_{tweet_id}")
    kind = "single" if len(media_items) == 1 else "gallery"
    return Result(service=SERVICE, kind=kind, media=media_items, title=title,
                  author=author, source_url=url, filename_hint=hint,
                  thumbnail=thumbs[0] if thumbs else None)


def _walk_tweets(obj):
    """Recursively yield tweet dicts that carry mediaDetails."""
    if isinstance(obj, dict):
        if obj.get("id_str") and "mediaDetails" in obj:
            yield obj
        for v in obj.values():
            yield from _walk_tweets(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_tweets(v)


def extract_timeline(ctx: Context, url: str, limit: int = 30):
    """Twitter/X profile -> Playlist of latest posts with media.

    Uses the public syndication timeline (no auth). Empty for protected
    accounts or when X restricts the widget."""
    from ..models import Playlist
    m = PROFILE_PATTERNS[0].match(url)
    if not m:
        raise ExtractionError("not a Twitter/X profile URL", SERVICE)
    handle = m.group(1)
    r = ctx.get(f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}",
                headers={"Referer": "https://platform.twitter.com/"})
    if r.status_code != 200:
        raise ExtractionError(f"could not load timeline (HTTP {r.status_code})", SERVICE)
    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if not nd:
        raise ExtractionError("timeline widget returned no data (rate-limited?)", SERVICE)
    try:
        data = json.loads(nd.group(1))
    except ValueError as e:
        raise ExtractionError(f"unexpected timeline response: {e}", SERVICE) from e

    entries, seen = [], set()
    for tweet in _walk_tweets(data):
        tid = tweet["id_str"]
        if tid in seen:
            continue
        seen.add(tid)
        media_items, thumbs = [], []
        for entry in tweet.get("mediaDetails") or []:
            _append_entry(entry, media_items, thumbs)
        if not media_items:
            continue
        author = (tweet.get("user") or {}).get("screen_name") or handle
        entries.append(Result(
            service=SERVICE, kind="single" if len(media_items) == 1 else "gallery",
            media=media_items, title=(tweet.get("text") or "").strip() or None,
            author=author, source_url=f"https://x.com/{author}/status/{tid}",
            filename_hint=safe_filename(f"twitter_{author}_{tid}"),
            thumbnail=thumbs[0] if thumbs else None))
        if len(entries) >= limit:
            break
    if not entries:
        raise ExtractionError("no media posts in this timeline", SERVICE)
    return Playlist(service=SERVICE, entries=entries, author=handle, source_url=url)
