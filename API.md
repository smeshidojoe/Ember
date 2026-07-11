# Ember — API reference (for developers)

Public Python API of the `ember` package. All public functions are typed
(`py.typed` shipped), so IDEs show signatures and docstrings on hover.

```python
import ember
```

## Extraction

### `extract(url, *, timeout=15.0, proxies=None, cookies=None, cookies_from_browser=None, browser_profile=None, session=None) -> Result`
Extract direct media links + metadata from a post URL.
- **url** `str` — post/track/video link.
- **timeout** `float` — per-request timeout, seconds.
- **proxies** `dict | None` — requests-style, e.g. `{"https": "http://host:port"}`.
- **cookies** `dict | None` — `{name: value}` (auth: NSFW/private).
- **cookies_from_browser** `str | None` — `"firefox"`, `"vivaldi"`, `"chrome"`, …
- **browser_profile** `str | None` — profile name for the browser.
- **session** `requests.Session | None` — bring your own session.
- **returns** `Result`.
- **raises** `UnsupportedUrlError` (use yt-dlp fallback), `NetworkError`, `ExtractionError`.

### `extract_playlist(url, **same kwargs) -> Playlist`
Extract a set/playlist (currently SoundCloud sets). Single link → `Playlist` with one entry.

### `extract_timeline(url, *, limit=30, **same kwargs) -> Playlist`
List an author's latest posts by profile/channel URL. Returns a `Playlist` of
`Result`s (one per post/track/video), up to `limit`. Supported: SoundCloud, VK,
Twitch, Tumblr, Rutube, Vimeo, Pinterest, Twitter/X, Instagram. Instagram and
Twitter/X may need cookies or a non-blocked IP.

### `can_extract(url) -> bool`
True if the URL matches a supported service (else hand it to yt-dlp).

### `supports_playlist(url) -> bool` / `supports_timeline(url) -> bool`
Whether playlist / author-timeline extraction is available for the URL.

### `supports_playlist(url) -> bool`
True if playlist extraction is available for the URL.

### `supported_services() -> list[str]`
List of service names.

## Download

### `download(result, out_dir=".", *, filename=None, ctx=None, max_height=None, concurrency=1, on_progress=None, audio_only=False, embed_metadata=False, subtitles=False, thumbnail=False, write_info=False) -> list[str]`
Download a whole `Result`. Returns paths of written files.
- **filename** `str | None` — base name without extension (default: from metadata).
- **max_height** `int | None` — cap quality (e.g. `720`).
- **concurrency** `int` — parallel HLS segments.
- **on_progress** `Callable[[DownloadProgress], None] | None`.
- **audio_only** `bool` — extract audio (needs ffmpeg).
- **embed_metadata** `bool` — write title/author (needs ffmpeg).
- **subtitles** `bool` — also download subtitle tracks.
- **thumbnail** `bool` — also save the cover image.
- **write_info** `bool` — save a `{base}.info.json` sidecar with all metadata.
- HLS: single stream assembles without ffmpeg; separate audio/video and `kind="merge"` need ffmpeg.

### `download_media(media, out_path, *, ctx=None, max_height=None, concurrency=1, on_progress=None, resume=True, audio_only=False, meta=None) -> str`
Download one `Media`. Returns the actual path (extension may become `.ts` without ffmpeg).

### `available_qualities(media, ctx=None) -> list[int]`
Available heights, e.g. `[1080, 720, 480]`. Parses the HLS master for m3u8 media.

### `probe_size(media, ctx=None) -> int | None`
File size in bytes before downloading (from `Content-Length`). One request, no
body — same access profile as the real download.

### `ffmpeg_available() -> bool`
Whether `ffmpeg` is on PATH.

## Cookies

### `cookies_from_browser(browser, service=None, profile=None) -> dict`
Read cookies from a browser. Native reader (Firefox any OS, Chromium on Win/mac/Linux),
falling back to yt-dlp / browser_cookie3.
- **service** `str | None` — limit to that service's domains.
- **raises** `EmberError` (unsupported combo with no backend, or App-Bound Encryption).

## Data models

### `Result`
- `service: str` — service name.
- `kind: str` — `"single"` | `"merge"` (video+audio separate, needs ffmpeg) | `"gallery"`.
- `media: list[Media]`.
- `title: str | None`, `author: str | None`, `source_url: str`.
- `filename_hint: str | None` — safe base name.
- `thumbnail: str | None` — preview URL.
- `duration: float | None` — seconds, when the service reports it (video/audio services).
- `timestamp: int | None` — unix seconds of publication, when reported.
- `view_count: int | None`, `like_count: int | None` — when reported.
- `subtitles: list[Subtitle]`.
- `requires_merge: bool` (property) — True when `kind == "merge"`.
- `to_dict() -> dict`.

### `Media`
- `kind: str` — `"video"` | `"audio"` | `"photo"` | `"gif"`.
- `url: str`, `ext: str`, `quality: str | None`.
- `http_headers: dict` — **pass these to your downloader** (TikTok returns 403 without them).
- `variants: list[MediaVariant]` — other qualities (progressive).
- `to_dict() -> dict`.

### `MediaVariant`
- `url: str`, `height: int | None`, `quality: str | None`, `ext: str`.

### `Subtitle`
- `lang: str`, `url: str`, `ext: str` (usually `"vtt"`).

### `Playlist`
- `service: str`, `entries: list[Result]`, `title: str | None`, `author: str | None`, `source_url: str`.
- `to_dict() -> dict`.

### `DownloadProgress` (passed to `on_progress`)
- `downloaded: int`, `total: int | None`.
- `segments_done: int`, `segments_total: int | None`.
- `stage: str` — `"download"` | `"mux"` | `"metadata"`.
- `fraction: float | None` (property) — 0..1 or None if unknown.

Speed and ETA are not fields — compute them with your own timer (the CLI does
the same):

```python
import time
start = time.time()

def on_progress(p: ember.DownloadProgress):
    if not p.total:                      # HLS / unknown size: no % or ETA
        return
    speed = p.downloaded / (time.time() - start)        # bytes/sec
    eta = (p.total - p.downloaded) / speed if speed else 0
    print(f"{p.fraction*100:5.1f}%  {speed/1048576:.2f} MiB/s  ETA {int(eta)}s")

ember.download(result, "downloads/", on_progress=on_progress)
```

## Errors

`EmberError` (base) → `UnsupportedUrlError`, `NetworkError`, `ExtractionError`.
Catch `EmberError` to cover them all (e.g. to fall back to yt-dlp).

## Logging

Package logs to the `ember` logger (children `ember.router`, `ember.http`,
`ember.cookies`, `ember.download`). Silent by default (`NullHandler`). Enable:

```python
import logging
logging.basicConfig()
logging.getLogger("ember").setLevel(logging.INFO)   # or DEBUG
```

## Typical embedding pattern

```python
import ember

def fetch(url: str, out_dir: str):
    if not ember.can_extract(url):
        return run_ytdlp(url)                 # your fallback
    try:
        result = ember.extract(url)
    except ember.EmberError:
        return run_ytdlp(url)
    return ember.download(result, out_dir, concurrency=6,
                          on_progress=lambda p: print(p.fraction))
```

## Auto-generated HTML docs

Docstrings + type hints power IDE hovers and doc generators:

```bash
pip install pdoc
pdoc ember -o docs        # HTML site in ./docs
```
