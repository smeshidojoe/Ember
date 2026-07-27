**[🇷🇺 Русский](https://github.com/smeshidojoe/Ember/blob/main/README.RU.md)** · **[🇬🇧 English](https://github.com/smeshidojoe/Ember/blob/main/README.md)**

# Ember

An embeddable Python library and CLI for extracting and downloading media from social
platforms — a compact alternative to [cobalt](https://github.com/imputnet/cobalt). Given a
post URL it returns **direct media URLs + metadata** and can **download by itself**
(including HLS), without requiring yt-dlp.

The only required dependency is `requests`. Python 3.9+.

## Supported services

**18 services:**

| Service | Extracts | Notes |
|---|---|---|
| TikTok | videos, photo posts, music | |
| Twitter/X | videos, GIFs, photos | NSFW tweets need cookies |
| Instagram | posts, Reels, carousels, Stories, highlights | anonymously often preview-only; Stories/highlights need cookies |
| Reddit | videos, GIFs, images, galleries | may be IP-blocked on VPN/datacenter |
| Vimeo | videos (mp4/HLS) | |
| SoundCloud | tracks, sets | premium tracks give a 30s preview anonymously |
| Pinterest | video/image pins | |
| Tumblr | video, audio, photos | |
| Bluesky | video (HLS), images, GIFs | |
| Newgrounds | video, audio | anti-bot on some IPs |
| Rutube | videos (HLS) | |
| OK.ru | videos | may need a normal (non-datacenter) IP |
| VK / VK Video | videos, clips | closed groups / private videos need cookies |
| Facebook | videos, Reels | usually needs cookies |
| Twitch | clips only | not VODs/streams |
| Pornhub | videos | age wall bypassed automatically |
| XVideos | videos (HLS) | mirror domains supported |
| RedGifs | videos | |

> Reddit, Newgrounds and OK.ru block anonymous requests from datacenter/VPN addresses
> (they work on a normal home IP); Instagram and Facebook may require cookies for full
> access. See "Limitations".

## Installation

```bash
pip install ember-dl
```

The distribution is named `ember-dl`, the import name stays `ember`:

```python
import ember
```

Latest development version straight from Git:

```bash
pip install git+https://github.com/smeshidojoe/Ember.git
```

After installation both the Python API (`import ember`) and the `ember` command are available.

`--cookies-from-browser` works out of the box (no extra deps) for **Firefox** (any OS)
and **Chromium-family browsers on Windows** (Vivaldi, Opera, and non-ABE Chrome/Edge/Brave).
Installing `pip install yt-dlp` is only an optional fallback for cases the built-in reader
doesn't cover (e.g. Chromium on macOS/Linux).

Downloading HLS with separate tracks, muxing video+audio, embedding metadata and
audio-only mode require **ffmpeg** in `PATH` (not needed for direct mp4 or plain HLS).

## Quick start — Python

```python
import ember

# 1) get direct links and metadata
result = ember.extract("https://vimeo.com/76979871")
print(result.title, result.author, result.thumbnail)
for m in result.media:
    print(m.kind, m.quality, m.url)

# 2) download with Ember itself (no yt-dlp needed)
def on_progress(p: ember.DownloadProgress):
    if p.fraction is not None:
        print(f"{p.fraction*100:.0f}%")

paths = ember.download(
    result, "downloads/",
    max_height=720,        # cap quality
    concurrency=6,         # parallel HLS segments
    on_progress=on_progress,
    embed_metadata=True,   # write title/author (ffmpeg)
)
```

Link check and quality list:

```python
ember.can_extract(url)                 # is the link supported
ember.available_qualities(result.media[0])   # e.g. [1080, 720, 480]
ember.ffmpeg_available()               # is ffmpeg present

# building a quality selector: hide rows and choose the order
ember.available_qualities(result.media[0], exclude=[360, 480])   # drop those rows
ember.available_qualities(result.media[0], ascending=True)       # low -> high
```

Playlists (SoundCloud sets for now):

```python
if ember.supports_playlist(url):
    for entry in ember.extract_playlist(url).entries:
        ember.download(entry, "downloads/")
```

Author timeline — latest posts by profile/channel URL (Twitter/X, Instagram,
Vimeo, SoundCloud, Pinterest, Tumblr, Rutube, VK, Twitch):

```python
if ember.supports_timeline(url):                 # e.g. "https://x.com/nasa"
    for entry in ember.extract_timeline(url, limit=30).entries:
        ember.download(entry, "downloads/")
```

Instagram story highlights of a profile (one entry per collection, needs cookies):

```python
pl = ember.extract_highlights("https://www.instagram.com/USER/",
                              cookies_from_browser="firefox")
for entry in pl.entries:
    print(entry.title, len(entry.media))
    ember.download(entry, "downloads/")
```

Long batches — don't redo finished files, and stay polite on the network:

```python
ember.download(result, "downloads/",
               skip_existing=True,      # keep what is already on disk
               rate_limit=1_000_000)    # ~1 MB/s total, shared by all threads
```

## Quick start — command line

Quote the URL. **Without `-d` the command only prints links and metadata — no download
starts.** Flag order does not matter.

```bash
# show direct links and metadata
ember "https://x.com/user/status/123456789"

# same, as JSON
ember "https://www.tiktok.com/@user/video/7123456789" --json

# DOWNLOAD: -d enables it (name from the site, current folder)
ember -d "https://vimeo.com/76979871"

# custom file name (-o) and folder (-p)
ember -d -o myclip -p downloads "https://vimeo.com/76979871"

# cap quality, fetch HLS in 6 threads
ember -d -p downloads --max-height 720 --concurrency 6 "https://rutube.ru/video/<id>/"

# audio only + write metadata into the file (needs ffmpeg)
ember -d --audio-only --embed-metadata "https://soundcloud.com/user/track"

# a whole playlist / set
ember -d --playlist "https://soundcloud.com/user/sets/name"

# cookies (NSFW tweets, private Instagram, closed VK groups)
ember "https://x.com/user/status/123" --cookies "auth_token=...; ct0=..."
ember "https://x.com/user/status/123" --cookies-from-browser firefox

# Instagram story highlights of a profile (needs cookies)
ember --highlights --cookies-from-browser firefox "https://www.instagram.com/USER/"

# resume a batch politely: skip finished files, cap speed at ~1 MB/s
ember -d -a links.txt --skip-existing --rate-limit 1000000
```

## Flags cheat sheet

| Flag | Meaning |
|---|---|
| `-d`, `--download` | enable downloading (without it — only print links) |
| `-o`, `--output NAME` | file name without extension, or a template: `%(title)s`/`%(author)s`/`%(service)s`/`%(id)s`; implies download |
| `-p`, `--path DIR` | target folder (default: current folder); implies download |
| `-a`, `--batch-file FILE` | read links from a file (one per line, `#` comments; `-` = stdin) |
| `-F`, `--list-formats` | list available qualities and exit (no download) |
| `-v`, `--verbose` | log to stderr; `-vv` for debug |
| `--json` | print metadata as JSON |
| `--max-height N` | cap quality by height (e.g. `720`) |
| `--audio-only` | keep audio only (needs ffmpeg); implies download |
| `--subs` | also download subtitle tracks; implies download |
| `--concurrency N` | parallel HLS segments (default `1`) |
| `--embed-metadata` (`--metadata`) | write title/author into the file (needs ffmpeg); implies download |
| `--thumbnail` | also save the cover image; implies download |
| `--write-info` | save a `{name}.info.json` sidecar with all metadata; implies download |
| `--skip-existing` | keep files that are already on disk instead of re-downloading |
| `--rate-limit N` | cap total speed in bytes/sec (e.g. `1000000` ≈ 1 MB/s), shared across threads |
| `--size` | show file size before downloading (one extra request) |
| `--playlist` | treat as a set (SoundCloud sets) |
| `--timeline` | treat the URL as a profile/channel, list latest posts |
| `--highlights` | treat the URL as a profile, list its story highlights (Instagram; needs cookies) |
| `--limit N` | max items for `--timeline` / `--highlights` (default `30`) |
| `--proxy URL` | proxy for all requests, e.g. `http://host:port` (helps with IP-blocked sites) |
| `--timeout SEC` | per-request timeout, seconds (default `15`) |
| `--cookies "a=1; b=2"` | cookies as a string |
| `--cookies-file FILE` | cookies.txt in Netscape format (like yt-dlp) |
| `--cookies-from-browser B` | cookies from a browser: brave/chrome/chromium/edge/firefox/opera/safari/vivaldi/whale |
| `--browser-profile P` | browser profile for the previous flag |
| `--list-services` | print supported services and exit |
| `--version` | print the version and exit |
| `-h`, `--help` | show help and exit |

Terminal help — `ember --help` or `ember -h`:

```
usage: ember [-h] [--version] [--list-services] [-a FILE] [--json] [-F] [-v]
             [--timeout SEC] [-d] [-o NAME] [-p DIR] [--max-height N]
             [--audio-only] [--subs] [--thumbnail] [--write-info] [--size]
             [--concurrency N] [--skip-existing] [--rate-limit N]
             [--embed-metadata] [--playlist] [--timeline] [--highlights]
             [--limit N] [--proxy URL] [--cookies "name=value; ..."]
             [--cookies-file cookies.txt] [--cookies-from-browser BROWSER]
             [--browser-profile PROFILE] [url]
```

## How it works

Ember calls the same "internal" service APIs that the site itself uses in the browser,
pulls direct media links and metadata out of the response, and packs them into a `Result`
object. Each service is a small standalone module in `ember/services/`. HLS manifests are
parsed by a built-in parser, segments are fetched and assembled into a file (with ffmpeg —
remuxed into `.mp4`).

## Limitations

- **Instagram** — anonymously often returns only a preview; pass cookies for full quality.
- **Reddit, Newgrounds, OK.ru** — block anonymous requests from datacenter/VPN IPs; they
  work on a normal home IP. `proxies=` / a different IP helps.
- **Facebook** — public video usually requires cookies.
- **Twitter/X** — NSFW tweets require account cookies (`auth_token` and `ct0`).
- Full YouTube support is out of scope — use yt-dlp for it.

## Credits

Extraction methods follow the approaches of
[imputnet/cobalt](https://github.com/imputnet/cobalt).
