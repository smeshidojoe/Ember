from ember import hls

MASTER = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="en",DEFAULT=YES,URI="audio/en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1",AUDIO="aud"
360/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080,CODECS="avc1",AUDIO="aud"
1080/video.m3u8
"""

MEDIA_TS = """#EXTM3U
#EXT-X-TARGETDURATION=6
#EXTINF:6.0,
seg0.ts
#EXTINF:6.0,
seg1.ts
#EXT-X-ENDLIST
"""

MEDIA_FMP4 = """#EXTM3U
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4.0,
seg0.m4s
#EXT-X-ENDLIST
"""


def test_parse_master_variants():
    m = hls.parse_master(MASTER, "https://cdn.example.com/v/master.m3u8")
    assert len(m.variants) == 2
    heights = sorted(v.height for v in m.variants)
    assert heights == [360, 1080]
    assert m.variants[0].url.startswith("https://cdn.example.com/v/")


def test_master_best_and_cap():
    m = hls.parse_master(MASTER, "https://cdn.example.com/v/master.m3u8")
    assert m.best().height == 1080
    assert m.best(max_height=720).height == 360   # ближайший не выше 720


def test_master_audio_track():
    m = hls.parse_master(MASTER, "https://cdn.example.com/v/master.m3u8")
    v = m.best()
    audio = m.audio_url_for(v)
    assert audio.endswith("audio/en.m3u8")


def test_parse_media_ts():
    md = hls.parse_media(MEDIA_TS, "https://cdn.example.com/v/360/video.m3u8")
    assert not md.is_fmp4
    assert md.init_url is None
    assert len(md.segments) == 2
    assert md.segments[0].endswith("/360/seg0.ts")


def test_parse_media_fmp4():
    md = hls.parse_media(MEDIA_FMP4, "https://cdn.example.com/v/x/video.m3u8")
    assert md.is_fmp4
    assert md.init_url.endswith("/x/init.mp4")
    assert len(md.segments) == 1


def test_looks_like_media_playlist():
    assert hls.looks_like_media_playlist(MEDIA_TS)
    assert not hls.looks_like_media_playlist(MASTER)
