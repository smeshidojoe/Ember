from ember.models import Media, MediaVariant, Result, safe_filename
from ember.download import _pick_progressive_url


def test_safe_filename():
    assert safe_filename('a/b:c*?"<>|d') == "a_b_c______d"
    assert safe_filename("  ...  ") == "media"
    assert len(safe_filename("x" * 500)) == 120


def test_result_to_dict_roundtrip():
    m = Media(kind="video", url="http://x/v.mp4", quality="1080p",
              variants=[MediaVariant(url="http://x/720.mp4", height=720)])
    r = Result(service="vk", kind="single", media=[m], title="t", author="a",
               thumbnail="http://x/thumb.jpg")
    d = r.to_dict()
    assert d["thumbnail"] == "http://x/thumb.jpg"
    assert d["media"][0]["variants"][0]["height"] == 720


def test_pick_progressive_url_caps_quality():
    m = Media(kind="video", url="http://x/1080.mp4", variants=[
        MediaVariant(url="http://x/1080.mp4", height=1080),
        MediaVariant(url="http://x/480.mp4", height=480),
    ])
    assert _pick_progressive_url(m, None) == "http://x/1080.mp4"
    assert _pick_progressive_url(m, 720) == "http://x/480.mp4"
    assert _pick_progressive_url(m, 1080) == "http://x/1080.mp4"
