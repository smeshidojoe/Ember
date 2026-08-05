import ember
from ember.router import _match_service


def test_supported_services_count():
    assert len(ember.supported_services()) == 19


def test_can_extract_positive():
    for url in [
        "https://www.tiktok.com/@u/video/7123456789012345678",
        "https://x.com/u/status/123",
        "https://vimeo.com/76979871",
        "https://rutube.ru/video/aabbccddeeff00112233445566778899/",
        "https://vk.com/video-1_2",
        "https://clips.twitch.tv/SomeSlug-abc",
    ]:
        assert ember.can_extract(url), url


def test_can_extract_negative():
    for url in ["https://youtube.com/watch?v=x", "https://example.com/"]:
        assert not ember.can_extract(url), url


def test_routing_targets():
    cases = {
        "https://vimeo.com/76979871": "vimeo",
        "https://soundcloud.com/u/track": "soundcloud",
        "https://soundcloud.com/u/sets/name": "soundcloud",
        "https://ok.ru/video/123": "ok",
        "https://vkvideo.ru/video1_2": "vk",
        "https://bsky.app/profile/a.b/post/xyz": "bluesky",
    }
    for url, svc in cases.items():
        assert _match_service(url).SERVICE == svc


def test_playlist_support():
    assert ember.supports_playlist("https://soundcloud.com/u/sets/name")
    assert not ember.supports_playlist("https://vimeo.com/76979871")


def test_timeline_support():
    for url in [
        "https://soundcloud.com/someartist",
        "https://www.twitch.tv/somechannel",
        "https://www.tumblr.com/someblog",
        "https://vimeo.com/someuser",
        "https://x.com/someone",
        "https://www.instagram.com/nasa/",
        "https://rutube.ru/channel/123/",
    ]:
        assert ember.supports_timeline(url), url
    # a post/video URL is not a profile
    assert not ember.supports_timeline("https://vimeo.com/76979871")
    assert not ember.supports_timeline("https://x.com/u/status/123")


def test_a_post_url_never_counts_as_a_profile():
    """Profile patterns are necessarily broad, so a post can slip into them.

    VK is the case that bit us: `vkvideo.ru/video150080649_456241625` matched
    the profile pattern because digits and `_` are part of \\w — but only with
    a POSITIVE owner id, since `-` is outside `[\\w.]`. Both spellings and both
    hosts are pinned here.
    """
    posts = [
        "https://vkvideo.ru/video150080649_456241625",   # положительный id
        "https://vkvideo.ru/video-208296598_456239378",  # отрицательный
        "https://vk.com/video150080649_456241625",
        "https://vk.com/video-208296598_456239378",
        "https://vkvideo.ru/clip150080649_456241625",
        "https://vkvideo.ru/video150080649_456241625_abc123",  # с access_key
    ]
    for url in posts:
        assert ember.can_extract(url), url
        assert not ember.supports_timeline(url), f"{url} принят за профиль"
        assert not ember.supports_highlights(url), f"{url} принят за профиль"


def test_profiles_still_route_to_timeline():
    """Обратная сторона: защита не должна съесть настоящие профили."""
    for url in ["https://vkvideo.ru/@durov", "https://vk.com/durov",
                "https://x.com/nasa", "https://www.instagram.com/nasa/"]:
        assert ember.supports_timeline(url), url


def test_playlist_urls_are_not_blocked_by_the_post_guard():
    """Сет SoundCloud намеренно совпадает и с PATTERNS, и с PLAYLIST_PATTERNS —
    защита «пост важнее профиля» не должна ломать наборы."""
    url = "https://soundcloud.com/u/sets/name"
    assert ember.can_extract(url)
    assert ember.supports_playlist(url)
