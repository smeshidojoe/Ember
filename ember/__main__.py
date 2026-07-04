"""CLI для быстрой проверки: python -m ember <url> [--json] [--cookies ...]"""

import argparse
import json
import sys

from . import EmberError, extract


def _parse_cookies_arg(raw: str) -> dict:
    """Разбирает строку вида "auth_token=abc; ct0=def" в словарь."""
    cookies = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, _, value = pair.partition("=")
            cookies[name.strip()] = value.strip()
    return cookies


def _parse_cookies_file(path: str) -> dict:
    """Читает cookies.txt (формат Netscape — его экспортируют
    браузерные расширения вроде Get cookies.txt, тот же формат у yt-dlp)."""
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


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ember",
        description="Извлечение прямых ссылок на медиа (TikTok, Twitter/X, Instagram, Reddit)")
    parser.add_argument("url", help="ссылка на пост")
    parser.add_argument("--json", action="store_true", help="вывести результат как JSON")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--cookies", metavar='"name=value; name2=value2"',
        help='cookies строкой, например "auth_token=...; ct0=..." для NSFW-твитов')
    parser.add_argument(
        "--cookies-file", metavar="cookies.txt",
        help="файл cookies.txt в формате Netscape (как у yt-dlp)")
    parser.add_argument(
        "--cookies-from-browser", metavar="chrome",
        help="взять cookies прямо из браузера (chrome/firefox/edge/brave/...), "
             "как yt-dlp --cookies-from-browser")
    parser.add_argument(
        "--browser-profile", metavar="Default",
        help="профиль браузера для --cookies-from-browser")
    args = parser.parse_args()

    cookies = {}
    if args.cookies_file:
        try:
            cookies.update(_parse_cookies_file(args.cookies_file))
        except OSError as e:
            print(f"не удалось прочитать файл cookies: {e}", file=sys.stderr)
            return 1
    if args.cookies:
        cookies.update(_parse_cookies_arg(args.cookies))

    try:
        result = extract(
            args.url,
            timeout=args.timeout,
            cookies=cookies or None,
            cookies_from_browser=args.cookies_from_browser,
            browser_profile=args.browser_profile,
        )
    except EmberError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"сервис:   {result.service}")
    print(f"тип:      {result.kind}")
    print(f"автор:    {result.author or '-'}")
    print(f"название: {(result.title or '-')[:100]}")
    print(f"файл:     {result.filename_hint}")
    for i, m in enumerate(result.media, 1):
        q = f" [{m.quality}]" if m.quality else ""
        print(f"  {i}. {m.kind}{q} .{m.ext}")
        print(f"     {m.url[:150]}")
        if m.http_headers:
            print(f"     заголовки для скачивания: {list(m.http_headers)}")
    if result.requires_merge:
        print("! видео и аудио раздельно — нужно объединить через ffmpeg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
