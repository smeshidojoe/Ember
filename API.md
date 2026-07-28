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

### `extract_many(urls, *, workers=6, **same kwargs) -> list[tuple]`
Extract many links in parallel. Returns `[(url, result, error), ...]` **in the
same order as the input**: on success `error` is None, on failure `result` is
None and `error` holds the `EmberError`. A single dead link never aborts the
batch. On a mixed batch this is ~2–3× faster than a loop.

```python
for url, res, err in ember.extract_many(links):
    if err:
        print("skip", url, err.reason)     # see Reason below
    else:
        ember.download(res, "downloads/")
```

`workers` — parallel requests; keep it modest, services rate-limit. The CLI
uses this automatically for `-a batch.txt` with several plain links.

### `extract_playlist(url, **same kwargs) -> Playlist`
Extract a set/playlist (currently SoundCloud sets). Single link → `Playlist` with one entry.

### `extract_timeline(url, *, limit=30, **same kwargs) -> Playlist`
List an author's latest posts by profile/channel URL. Returns a `Playlist` of
`Result`s (one per post/track/video), up to `limit`. Supported: Twitter/X,
Instagram, Vimeo, SoundCloud, Pinterest, Tumblr, Rutube, VK, Twitch. Instagram
and Twitter/X may need cookies or a non-blocked IP.

### `extract_highlights(url, *, limit=30, **same kwargs) -> Playlist`
List a profile's story highlights (the pinned covers above the posts) from a
**profile URL**. One entry per highlight collection; each entry is a `"gallery"`
`Result` holding that collection's stories, with `title` set to the highlight's
name. Currently Instagram only, and it **requires account cookies**.

```python
pl = ember.extract_highlights("https://www.instagram.com/USER/", limit=5,
                              cookies_from_browser="firefox")
for entry in pl.entries:
    print(entry.title, len(entry.media))
    ember.download(entry, "downloads/")
```

### `can_extract(url) -> bool`
True if the URL matches a supported service (else hand it to yt-dlp).

### `supports_playlist(url) -> bool`
True **only if the URL really is a playlist/set** — a single track/video gives
`False`. (`extract_playlist()` still accepts a single link and returns a
one-entry `Playlist`; this predicate stays strict so you can decide whether to
show playlist UI.)

### `supports_timeline(url) -> bool`
True if the URL is a profile/channel with author-timeline support.

### `supports_highlights(url) -> bool`
True if the URL is a profile with story-highlight support (Instagram).

### `supported_services() -> list[str]`
List of service names (18).

## Download

### `download(result, out_dir=".", *, filename=None, ctx=None, max_height=None, concurrency=1, on_progress=None, audio_only=False, embed_metadata=False, subtitles=False, thumbnail=False, write_info=False, skip_existing=False, rate_limit=None) -> list[str]`
Download a whole `Result`. Returns paths of written files.
- **filename** `str | None` — base name without extension (default: from metadata).
- **max_height** `int | None` — cap quality (e.g. `720`).
- **concurrency** `int` — parallel workers. For a single media it fetches HLS
  segments in parallel; for a `"gallery"` it downloads the items in parallel
  (output order and names stay stable regardless of the value).
- **on_progress** `Callable[[DownloadProgress], None] | None`.
- **audio_only** `bool` — extract audio (needs ffmpeg).
- **embed_metadata** `bool` — write title/author (needs ffmpeg). Applies to
  video/audio only; photos are left byte-for-byte untouched.
- **subtitles** `bool` — also download subtitle tracks.
- **thumbnail** `bool` — also save the cover image.
- **write_info** `bool` — save a `{base}.info.json` sidecar with all metadata.
- **skip_existing** `bool` — if the target file already exists, keep it and skip
  the download (the path is still returned, so the result list stays complete).
- **rate_limit** `float | None` — cap **total** download speed in bytes/sec
  (e.g. `1_000_000` ≈ 1 MB/s). One budget is shared by every thread of the call,
  so raising `concurrency` does not multiply the cap.
- HLS: single stream assembles without ffmpeg; separate audio/video and `kind="merge"` need ffmpeg.
- A failing item in a gallery is logged and skipped; a single-media failure raises.

### `download_media(media, out_path, *, ctx=None, max_height=None, concurrency=1, on_progress=None, resume=True, audio_only=False, skip_existing=False, limiter=None, meta=None) -> str`
Download one `Media`. Returns the actual path (extension may become `.ts` without ffmpeg).
- **resume** `bool` — reuse a leftover `.part` via HTTP Range. A `.part` is kept
  after a network failure (so the next run resumes) and removed on Ctrl+C.
- **limiter** `RateLimiter | None` — share one speed budget across several calls.
- **meta** `dict | None` — `{"title": ..., "artist": ...}` written into the
  container (needs ffmpeg). `download()` fills this from the `Result` when
  `embed_metadata=True`.

Both download functions take **ctx** `Context | None` — reuse the context from
`extract()` (or build one with `ember.http.make_context()`) to keep the same
session, cookies, proxy and timeout for the download. Omitted, a fresh
cookie-less context is created, which fails for CDNs that require the
extraction session.

### `RateLimiter(rate)`
Token bucket capping total bytes/sec, safe to share across threads. `download()`
builds one internally from `rate_limit=`; construct it yourself only to cap
several `download_media()` calls together:

```python
limiter = ember.RateLimiter(500_000)          # 500 KB/s for both files
ember.download_media(a, "a.mp4", limiter=limiter)
ember.download_media(b, "b.mp4", limiter=limiter)
```

### `available_qualities(media, ctx=None, *, exclude=(), ascending=False) -> list[int]`
Available heights, e.g. `[1080, 720, 480]`. Parses the HLS master for m3u8 media.
- **exclude** — heights to leave out, e.g. `exclude=[360, 480]` when your UI hides
  those rows. Accepts ints or strings; heights the media doesn't have are ignored.
  Excluding everything returns `[]` — the caller decides what to show then.
- **ascending** — order low→high instead of the default high→low.

Any value from the list feeds straight back into `max_height=`:

```python
heights = ember.available_qualities(result.media[0], exclude=hidden)
if heights:
    ember.download(result, "downloads/", max_height=heights[0])   # best allowed
```

### `probe_size(media, ctx=None) -> int | None`
File size in bytes before downloading (from `Content-Length`). One request, no
body — same access profile as the real download.

### `ffmpeg_available() -> bool`
Whether `ffmpeg` is on PATH.

## Cookies

### `cookies_from_browser(browser, service=None, profile=None, domains=None) -> dict`
Read cookies from a browser. Native reader (Firefox any OS, Chromium on Win/mac/Linux),
falling back to yt-dlp / browser_cookie3.
- **service** `str | None` — limit to that service's domains. An unknown name
  raises `EmberError` (it does not silently return `{}`).
- **domains** `list[str] | None` — explicit domain substrings, e.g.
  `["youtube.com"]`. Works for sites Ember doesn't support; overrides `service`.
- **raises** `EmberError` — unknown service; browser running and locking its
  cookie DB; unsupported combo with no backend; App-Bound Encryption.

### `cookies_from_file(path) -> dict`
Parse a Netscape-format `cookies.txt` (yt-dlp / browser-extension export).
`extract()` also accepts the path directly:

```python
ember.extract(url, cookies="cookies.txt")     # dict or path both work
```

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
- `is_preview: bool` — True when the service only exposed a **truncated**
  version (SoundCloud Go+ hands out a 30s snippet of a full track anonymously).
  `duration` then describes the snippet, not the work.
- `full_duration: float | None` — length of the complete version, when known.
- `subtitles: list[Subtitle]`.

```python
if result.is_preview:
    print(f"only {result.duration}s of {result.full_duration}s available")
```
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
- `started: float` — `time.monotonic()` when this download began.
- `fraction: float | None` (property) — 0..1, or None if size unknown.
- `elapsed: float` (property) — seconds since start.
- `speed: float` (property) — average bytes/sec.
- `eta: float | None` (property) — seconds left, None if size unknown.

No timer of your own needed:

```python
def on_progress(p: ember.DownloadProgress):
    if p.fraction is None:                       # HLS / unknown size
        print(f"{p.downloaded/1048576:.1f} MiB  {p.speed/1048576:.2f} MiB/s")
        return
    print(f"{p.fraction*100:5.1f}%  {p.speed/1048576:.2f} MiB/s  ETA {int(p.eta)}s")

ember.download(result, "downloads/", on_progress=on_progress)
```

## Version

### `__version__ -> str`
Package version, e.g. `"0.8.1"`. Same value the CLI prints for `ember --version`.

## Errors

`EmberError` (base) → `UnsupportedUrlError`, `NetworkError`, `ExtractionError`.
Catch `EmberError` to cover them all (e.g. to fall back to yt-dlp).

### `ExtractionError.reason -> str`
Why it failed, in a form code can branch on — no need to parse the English
message. Values live on `ember.Reason`:

| `Reason.` | meaning | typical reaction |
|---|---|---|
| `NEEDS_AUTH` | cookies/login required | offer to sign in, pass cookies |
| `RESTRICTED` | age wall, members-only, paid tier | tell the user access is missing |
| `DELETED` | removed, private, never existed | drop the link |
| `GEOBLOCKED` | blocked in this region | retry through a proxy |
| `RATE_LIMITED` | HTTP 429 / throttled | back off and retry later |
| `IP_BLOCKED` | datacenter/VPN address refused | retry from another IP |
| `NO_MEDIA` | page loaded, holds nothing downloadable | drop the link |
| `LIVE` | a live stream, not a finished recording | wait for the recording |
| `FORMAT_CHANGED` | the service changed its response shape | report a bug |
| `UNKNOWN` | could not tell | fall back to yt-dlp |

```python
try:
    result = ember.extract(url)
except ember.ExtractionError as e:
    if e.needs_auth:                       # NEEDS_AUTH or RESTRICTED
        result = ember.extract(url, cookies_from_browser="firefox")
    elif e.reason == ember.Reason.RATE_LIMITED:
        retry_later(url)
    else:
        fall_back_to_ytdlp(url)
```

`ExtractionError.needs_auth` is a shortcut for "would cookies plausibly help?".
`reason` is always set: services declare it where they can tell, otherwise it is
inferred from Ember's own message text, defaulting to `UNKNOWN`.

## Logging

Package logs to the `ember` logger (children `ember.router`, `ember.http`,
`ember.cookies`, `ember.download`). Silent by default (`NullHandler`). Enable:

```python
import logging
logging.basicConfig()
logging.getLogger("ember").setLevel(logging.INFO)   # or DEBUG
```

## CLI

Everything above is reachable from the `ember` command; `ember --help` lists all
flags. Notable ones mirroring this API: `--highlights` (`extract_highlights`),
`--timeline`, `--playlist`, `--skip-existing`, `--rate-limit BYTES_PER_SEC`,
`--size` (`probe_size`), `--write-info`, `--thumbnail`, `--list-services`,
`--version`. Without `-d` the command only prints — nothing is downloaded.

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
