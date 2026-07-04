import ember
from ember.router import _match_service


def test_supported_services_count():
    assert len(ember.supported_services()) == 15


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
