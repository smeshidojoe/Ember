"""API-drift tests: run each service's parser against recorded responses.

The point is NOT to check that a site is reachable — it's to catch the day our
*parsing* stops matching the shape a service actually returns. Fixtures under
tests/fixtures/ are real responses captured once; these tests never touch the
network, so they stay fast and deterministic.

Refresh the fixtures when a service legitimately changes its format, then fix
the parser until these pass again.
"""

import json
import pathlib

import pytest

from ember.services import bluesky, imgur, soundcloud, twitch, vimeo, xvideos

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"fixture {name} is missing")
    text = path.read_text(encoding="utf-8")
    return text if name.endswith(".txt") else json.loads(text)


class FakeResp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.url = "https://example.test/"
        self.headers = {}

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class FakeCtx:
    """Context that replays queued payloads instead of doing HTTP."""

    def __init__(self, *payloads):
        self._queue = list(payloads)
        self.session = type("S", (), {"cookies": [], "headers": {}})()

    def _next(self):
        return FakeResp(self._queue.pop(0) if self._queue else {})

    def get(self, *a, **kw):
        return self._next()

    def post(self, *a, **kw):
        return self._next()

    def head_ok(self, *a, **kw):
        return False


# --------------------------------------------------------------------------
# SoundCloud
# --------------------------------------------------------------------------

def test_soundcloud_normal_track_is_not_a_preview():
    track = load("soundcloud_track.json")
    ctx = FakeCtx({"url": "https://cdn.test/audio.mp3"})
    res = soundcloud._track_result(ctx, track, "https://soundcloud.com/x/y")
    assert res.media and res.media[0].kind == "audio"
    assert res.title and res.author
    assert res.is_preview is False
    assert res.duration and res.duration > 60


def test_soundcloud_go_plus_track_is_flagged_as_preview():
    """policy=SNIP means a ~30s snippet of a much longer track."""
    track = load("soundcloud_snip.json")
    ctx = FakeCtx({"url": "https://cdn.test/audio.mp3"})
    res = soundcloud._track_result(ctx, track, "https://soundcloud.com/x/y")
    assert res.is_preview is True
    assert res.full_duration and res.full_duration > res.duration


# --------------------------------------------------------------------------
# Imgur
# --------------------------------------------------------------------------

def test_imgur_single_media():
    ctx = FakeCtx(load("imgur_single.json"))
    res = imgur.extract(ctx, "https://imgur.com/dqOyj")
    assert res.kind == "single"
    assert len(res.media) == 1
    assert res.media[0].url.startswith("http")


def test_imgur_album_becomes_a_gallery():
    ctx = FakeCtx(load("imgur_album.json"))
    res = imgur.extract(ctx, "https://imgur.com/gallery/BcnjcsK")
    assert res.kind == "gallery"
    assert len(res.media) > 1
    assert all(m.url.startswith("http") for m in res.media)


# --------------------------------------------------------------------------
# Twitch
# --------------------------------------------------------------------------

def test_twitch_clip_parses_qualities_and_signs_the_url():
    info = load("twitch_clip.json")
    token = {"data": {"clip": {"playbackAccessToken":
                               {"value": "TOKEN", "signature": "SIG"}}}}
    ctx = FakeCtx(info, token)
    res = twitch.extract(ctx, "https://clips.twitch.tv/GoodAlertBurritoTheTarFu")
    assert res.media[0].ext == "mp4"
    assert "sig=SIG" in res.media[0].url and "token=TOKEN" in res.media[0].url
    assert res.title and res.author


def test_twitch_vod_returns_a_signed_hls_playlist():
    info = load("twitch_vod.json")
    token = {"data": {"videoPlaybackAccessToken":
                      {"value": "TOKEN", "signature": "SIG"}}}
    ctx = FakeCtx(info, token)
    res = twitch.extract(ctx, "https://www.twitch.tv/videos/2818023920")
    assert res.media[0].ext == "m3u8"
    assert "usher.ttvnw.net" in res.media[0].url
    assert "sig=SIG" in res.media[0].url
    assert res.duration and res.duration > 0


# --------------------------------------------------------------------------
# Vimeo / XVideos / Bluesky
# --------------------------------------------------------------------------

def test_vimeo_config_yields_playable_media():
    ctx = FakeCtx(load("vimeo_config.json"))
    res = vimeo.extract(ctx, "https://vimeo.com/76979871")
    assert res.media and res.media[0].url.startswith("http")
    assert res.title


def test_xvideos_player_vars_still_parse():
    """The page only ever gives us html5player.setX(...) lines."""
    ctx = FakeCtx(load("xvideos_player.txt"))
    res = xvideos.extract(ctx, "https://www.xvideos.com/video.abc/1/2/t")
    assert res.media[0].url.startswith("http")
    assert res.title


def test_bluesky_post_media():
    ctx = FakeCtx(load("bluesky_post.json"))
    res = bluesky.extract(
        ctx, "https://bsky.app/profile/did:plc:test/post/abc123")
    assert res.media
    assert all(m.url.startswith("http") for m in res.media)
