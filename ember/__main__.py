"""Ember CLI.

Show links:     ember "URL"
Download:       ember -d "URL"                 (site-derived name, current folder)
Custom name:    ember -d -o myclip "URL"
Custom folder:  ember -d -p downloads "URL"
Installed entry point is `ember`; `python -m ember` works too.
"""

import argparse
import re
import sys
import time

from . import (DownloadProgress, EmberError, available_qualities, download,
               extract, extract_playlist, supports_playlist)
from .http import make_context

# browsers understood by --cookies-from-browser (same set as yt-dlp)
BROWSERS = ["brave", "chrome", "chromium", "edge", "firefox",
            "opera", "safari", "vivaldi", "whale"]

# options that take a value (can accidentally swallow the URL) + example values
_VALUE_OPTS = {
    "-o": "myfile", "--output": "myfile", "-p": "downloads", "--path": "downloads",
    "--max-height": "720", "--concurrency": "6", "--timeout": "20",
    "--proxy": "http://host:port", "--cookies": '"a=1; b=2"',
    "--cookies-file": "cookies.txt", "--cookies-from-browser": "firefox",
    "--browser-profile": "Default",
}


def _parse_cookies_arg(raw: str) -> dict:
    cookies = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, _, value = pair.partition("=")
            cookies[name.strip()] = value.strip()
    return cookies


def _parse_cookies_file(path: str) -> dict:
    cookies = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
    return cookies


def _make_progress_printer():
    """Returns a callback that draws a simple one-line progress indicator."""
    state = {"last": 0.0}

    def cb(p: DownloadProgress):
        now = time.time()
        if p.stage != "download":
            print(f"\r  {p.stage}…", end="", flush=True)
            return
        if now - state["last"] < 0.1:
            return
        state["last"] = now
        mb = p.downloaded / 1048576
        if p.total:
            print(f"\r  {p.fraction * 100:5.1f}%  {mb:7.1f} MB", end="", flush=True)
        elif p.segments_total:
            print(f"\r  segment {p.segments_done}/{p.segments_total}  {mb:7.1f} MB",
                  end="", flush=True)
        else:
            print(f"\r  {mb:7.1f} MB", end="", flush=True)

    return cb


def _print_result(result) -> None:
    print(f"service:  {result.service}")
    print(f"type:     {result.kind}")
    print(f"author:   {result.author or '-'}")
    print(f"title:    {(result.title or '-')[:100]}")
    print(f"filename: {result.filename_hint}")
    if result.thumbnail:
        print(f"thumb:    {result.thumbnail[:100]}")
    for i, m in enumerate(result.media, 1):
        q = f" [{m.quality}]" if m.quality else ""
        print(f"  {i}. {m.kind}{q} .{m.ext}")
        print(f"     {m.url[:150]}")
        qs = available_qualities(m) if m.variants else []
        if qs:
            print(f"     qualities: {qs}")
    if result.requires_merge:
        print("! video and audio are separate — downloading needs ffmpeg")


class _Parser(argparse.ArgumentParser):
    """Turns confusing argparse errors into short, clear CLI messages
    (especially when a value-taking option accidentally swallows the URL)."""

    def _fail(self, text: str):
        # короткое сообщение без простыни usage
        sys.stderr.write(f"ember: error: {text}\n")
        self.exit(2)

    def error(self, message):
        argv = sys.argv[1:]
        url = next((a for a in argv if a.startswith(("http://", "https://"))), None)

        # 1) ссылка «съедена» как значение опции (напр. --cookies-from-browser URL)
        if url:
            i = argv.index(url)
            prev = argv[i - 1] if i > 0 else None
            if prev in _VALUE_OPTS:
                sample = _VALUE_OPTS[prev]
                self._fail(
                    f'"{prev}" needs a value, but your link was taken as its value.\n'
                    f'  put the value after "{prev}", e.g.:  '
                    f'ember {prev} {sample} "{url}"\n'
                    f'  or just show links:                 ember "{url}"')

        # 2) опция требует значение, но его нет (напр. в конце строки)
        m = re.search(r"argument ([^:]+): expected one argument", message)
        if m:
            opt = m.group(1).split("/")[-1].strip()
            sample = _VALUE_OPTS.get(opt, "<value>")
            self._fail(f'"{opt}" needs a value, e.g.:  ember {opt} {sample} "URL"')

        # 3) ссылка есть, но не встала как позиционный аргумент
        if "required: url" in message and url:
            self._fail(f'put the link as the last argument, e.g.:  ember "{url}"')

        super().error(message)


def _build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="ember",
                description="Extract and download media (a cobalt-like library)")
    p.add_argument("url", help="link to a post / track / video")
    p.add_argument("--json", action="store_true", help="print metadata as JSON")
    p.add_argument("--timeout", type=float, default=15.0, metavar="SEC",
                   help="per-request timeout, seconds (default 15)")
    # download
    p.add_argument("-d", "--download", action="store_true",
                   help="download the media (otherwise only links are shown)")
    p.add_argument("-o", "--output", metavar="NAME",
                   help="output file name without extension "
                        "(default: taken from the site); implies --download")
    p.add_argument("-p", "--path", metavar="DIR",
                   help="target folder (default: current folder); implies --download")
    p.add_argument("--max-height", type=int, metavar="N",
                   help="cap quality by height, e.g. 720")
    p.add_argument("--audio-only", action="store_true",
                   help="keep audio only (needs ffmpeg); implies --download")
    p.add_argument("--concurrency", type=int, default=1, metavar="N",
                   help="parallel HLS segments (default 1)")
    p.add_argument("--embed-metadata", "--metadata", action="store_true",
                   dest="embed_metadata",
                   help="write title/author into the file (needs ffmpeg); implies --download")
    p.add_argument("--playlist", action="store_true",
                   help="treat as a playlist/set (SoundCloud sets)")
    p.add_argument("--proxy", metavar="URL",
                   help="proxy for all requests, e.g. http://host:port "
                        "(helps with IP-blocked sites)")
    # cookies
    p.add_argument("--cookies", metavar='"name=value; ..."',
                   help="cookies as a string (NSFW tweets, private Instagram)")
    p.add_argument("--cookies-file", metavar="cookies.txt",
                   help="cookies.txt in Netscape format (like yt-dlp)")
    p.add_argument("--cookies-from-browser", metavar="BROWSER", choices=BROWSERS,
                   help="read cookies from a browser; one of: " + ", ".join(BROWSERS))
    p.add_argument("--browser-profile", metavar="PROFILE",
                   help="browser profile for --cookies-from-browser")
    return p


def _report_error(msg: str, prefix: str = "error") -> None:
    """Печатает ошибку и, если уместно, подсказку с CLI-флагами (не с Python-API)."""
    print(f"{prefix}: {msg}", file=sys.stderr)
    low = msg.lower()
    if "cookie" in low and "could not read cookies" not in low and "app-bound" not in low:
        print('hint: pass cookies with  --cookies "name=value; ..."  |  '
              "--cookies-file cookies.txt  |  --cookies-from-browser firefox",
              file=sys.stderr)
    if "proxy" in low or "network policy" in low:
        print("hint: try another IP with  --proxy http://host:port", file=sys.stderr)


def main() -> int:
    args = _build_parser().parse_args()

    cookies = {}
    if args.cookies_file:
        try:
            cookies.update(_parse_cookies_file(args.cookies_file))
        except OSError as e:
            print(f"could not read cookies file: {e}", file=sys.stderr)
            return 1
    if args.cookies:
        cookies.update(_parse_cookies_arg(args.cookies))

    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
    common = dict(timeout=args.timeout, proxies=proxies, cookies=cookies or None,
                  cookies_from_browser=args.cookies_from_browser,
                  browser_profile=args.browser_profile)

    do_download = (args.download or args.output or args.path
                   or args.audio_only or args.embed_metadata)

    try:
        if args.playlist or (do_download and supports_playlist(args.url)
                             and "/sets/" in args.url):
            playlist = extract_playlist(args.url, **common)
            results = playlist.entries
            print(f"playlist: {playlist.title or '-'} ({len(results)} items)")
        else:
            results = [extract(args.url, **common)]
    except EmberError as e:
        _report_error(str(e))
        return 1

    if args.json:
        import json
        payload = [r.to_dict() for r in results]
        print(json.dumps(payload if len(payload) > 1 else payload[0],
                         ensure_ascii=False, indent=2))
        return 0

    if not do_download:
        for r in results:
            _print_result(r)
            if len(results) > 1:
                print("-" * 40)
        return 0

    out_dir = args.path or "."
    # custom name applies only to a single result (not to a whole playlist)
    name = args.output if len(results) == 1 else None
    dl_ctx = make_context(timeout=args.timeout, proxies=proxies)
    cb = _make_progress_printer()
    for r in results:
        print(f"downloading: {(r.title or r.filename_hint)[:70]}")
        try:
            paths = download(r, out_dir, filename=name, ctx=dl_ctx,
                             max_height=args.max_height,
                             concurrency=args.concurrency, on_progress=cb,
                             audio_only=args.audio_only,
                             embed_metadata=args.embed_metadata)
        except EmberError as e:
            print()
            _report_error(str(e), prefix="  error")
            continue
        print("\r" + " " * 40, end="")
        for p in paths:
            print(f"\r  saved: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
