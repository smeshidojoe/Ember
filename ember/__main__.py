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
               extract, extract_playlist, extract_timeline, supports_playlist)
from .http import make_context

# browsers understood by --cookies-from-browser (same set as yt-dlp)
BROWSERS = ["brave", "chrome", "chromium", "edge", "firefox",
            "opera", "safari", "vivaldi", "whale"]

# options that take a value (can accidentally swallow the URL) + example values
_VALUE_OPTS = {
    "-o": "myfile", "--output": "myfile", "-p": "downloads", "--path": "downloads",
    "-a": "links.txt", "--batch-file": "links.txt",
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
    print(f"title:    {result.title or '-'}")
    print(f"filename: {result.filename_hint}")
    if result.thumbnail:
        print(f"thumb:    {result.thumbnail}")
    for i, m in enumerate(result.media, 1):
        q = f" [{m.quality}]" if m.quality else ""
        print(f"  {i}. {m.kind}{q} .{m.ext}")
        print(f"     {m.url}")
        qs = available_qualities(m) if m.variants else []
        if qs:
            print(f"     qualities: {qs}")
    if result.subtitles:
        print(f"subs:     {', '.join(s.lang for s in result.subtitles)}")
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
    p.add_argument("url", nargs="?", help="link to a post / track / video")
    p.add_argument("-a", "--batch-file", metavar="FILE",
                   help="read links from a file (one per line, '#' comments; '-' = stdin)")
    p.add_argument("--json", action="store_true", help="print metadata as JSON")
    p.add_argument("-F", "--list-formats", action="store_true",
                   help="list available qualities and exit (no download)")
    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="log to stderr; -vv for debug")
    p.add_argument("--timeout", type=float, default=15.0, metavar="SEC",
                   help="per-request timeout, seconds (default 15)")
    # download
    p.add_argument("-d", "--download", action="store_true",
                   help="download the media (otherwise only links are shown)")
    p.add_argument("-o", "--output", metavar="NAME",
                   help="output file name without extension, or a template with "
                        "%%(title)s/%%(author)s/%%(service)s/%%(id)s; implies --download")
    p.add_argument("-p", "--path", metavar="DIR",
                   help="target folder (default: current folder); implies --download")
    p.add_argument("--max-height", type=int, metavar="N",
                   help="cap quality by height, e.g. 720")
    p.add_argument("--audio-only", action="store_true",
                   help="keep audio only (needs ffmpeg); implies --download")
    p.add_argument("--subs", action="store_true",
                   help="also download subtitle tracks; implies --download")
    p.add_argument("--concurrency", type=int, default=1, metavar="N",
                   help="parallel HLS segments (default 1)")
    p.add_argument("--embed-metadata", "--metadata", action="store_true",
                   dest="embed_metadata",
                   help="write title/author into the file (needs ffmpeg); implies --download")
    p.add_argument("--playlist", action="store_true",
                   help="treat as a playlist/set (SoundCloud sets)")
    p.add_argument("--timeline", action="store_true",
                   help="treat the URL as a profile/channel and list latest posts")
    p.add_argument("--limit", type=int, default=30, metavar="N",
                   help="max items for --timeline (default 30)")
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
    """Print an error and, when relevant, a hint with CLI flags (not Python API)."""
    print(f"{prefix}: {msg}", file=sys.stderr)
    low = msg.lower()
    if "cookie" in low and "could not read cookies" not in low and "app-bound" not in low:
        print('hint: pass cookies with  --cookies "name=value; ..."  |  '
              "--cookies-file cookies.txt  |  --cookies-from-browser firefox",
              file=sys.stderr)
    if "proxy" in low or "network policy" in low:
        print("hint: try another IP with  --proxy http://host:port", file=sys.stderr)


def _setup_logging(verbose: int) -> None:
    if verbose <= 0:
        return
    import logging
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    lg = logging.getLogger("ember")
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG if verbose >= 2 else logging.INFO)


def _read_batch(path: str) -> list:
    src = sys.stdin if path == "-" else open(path, encoding="utf-8", errors="replace")
    try:
        urls = []
        for line in src:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
        return urls
    finally:
        if src is not sys.stdin:
            src.close()


def _render_name(template: str, result) -> str:
    if "%(" not in template:
        return template
    fields = {"title": result.title or "", "author": result.author or "",
              "uploader": result.author or "", "service": result.service,
              "id": result.filename_hint or ""}
    try:
        return template % fields
    except (KeyError, ValueError):
        return template


def _list_formats(result) -> None:
    print(f"# {result.service}: {result.title or result.filename_hint}")
    for i, m in enumerate(result.media, 1):
        qs = available_qualities(m)
        label = ", ".join(f"{h}p" for h in qs) if qs else (m.quality or "single")
        print(f"  {i}. {m.kind} .{m.ext}: {label}")
    for s in result.subtitles:
        print(f"  sub: {s.lang} ({s.ext})")


def main() -> int:
    # Windows-консоль часто cp1251/cp866 -> не-ASCII падает/рисуется квадратами.
    # Данные в str корректны; правим только вывод.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = _build_parser().parse_args()
    _setup_logging(args.verbose)

    urls = []
    if args.url:
        urls.append(args.url)
    if args.batch_file:
        try:
            urls += _read_batch(args.batch_file)
        except OSError as e:
            print(f"could not read batch file: {e}", file=sys.stderr)
            return 1
    if not urls:
        print("error: provide a URL or --batch-file FILE", file=sys.stderr)
        return 2

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

    do_download = (args.download or args.output or args.path or args.audio_only
                   or args.embed_metadata or args.subs)
    dl_ctx = make_context(timeout=args.timeout, proxies=proxies) if do_download else None
    cb = _make_progress_printer()
    rc = 0

    for url in urls:
        try:
            if args.timeline:
                pl = extract_timeline(url, limit=args.limit, **common)
                results = pl.entries
                print(f"timeline: {pl.author or '-'} ({len(results)} items)")
            elif args.playlist or (do_download and supports_playlist(url)
                                   and "/sets/" in url):
                playlist = extract_playlist(url, **common)
                results = playlist.entries
                print(f"playlist: {playlist.title or '-'} ({len(results)} items)")
            else:
                results = [extract(url, **common)]
        except EmberError as e:
            _report_error(str(e))
            rc = 1
            continue

        if args.json:
            import json
            payload = [r.to_dict() for r in results]
            print(json.dumps(payload if len(payload) > 1 else payload[0],
                             ensure_ascii=False, indent=2))
            continue
        if args.list_formats:
            for r in results:
                _list_formats(r)
            continue
        if not do_download:
            for r in results:
                _print_result(r)
                if len(results) > 1 or len(urls) > 1:
                    print("-" * 40)
            continue

        out_dir = args.path or "."
        single = len(results) == 1 and len(urls) == 1
        for r in results:
            print(f"downloading: {r.title or r.filename_hint}")
            if args.output and "%(" in args.output:
                name = _render_name(args.output, r)      # шаблон — на каждый результат
            elif args.output and single:
                name = args.output                        # литеральное имя — только один файл
            else:
                name = None                               # иначе имя с сайта
            try:
                paths = download(r, out_dir, filename=name, ctx=dl_ctx,
                                 max_height=args.max_height,
                                 concurrency=args.concurrency, on_progress=cb,
                                 audio_only=args.audio_only,
                                 embed_metadata=args.embed_metadata,
                                 subtitles=args.subs)
            except EmberError as e:
                print()
                _report_error(str(e), prefix="  error")
                rc = 1
                continue
            print("\r" + " " * 40, end="")
            for p in paths:
                print(f"\r  saved: {p}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
