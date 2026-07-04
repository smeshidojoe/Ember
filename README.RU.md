**[🇷🇺 Русский](https://github.com/smeshidojoe/Ember/blob/main/README.RU.md)** · **[🇬🇧 English](https://github.com/smeshidojoe/Ember/blob/main/README.md)**

# Ember

Встраиваемая Python-библиотека и CLI для извлечения и скачивания медиа из соцсетей —
компактный аналог [cobalt](https://github.com/imputnet/cobalt). По ссылке на пост
возвращает **прямые URL медиа + метаданные** и умеет **скачивать сам** (включая HLS),
не требуя yt-dlp.

Единственная обязательная зависимость — `requests`. Python 3.9+.

## Поддерживаемые сервисы

TikTok · Twitter/X · Instagram · Reddit · Vimeo · SoundCloud · Pinterest · Tumblr ·
Bluesky · Newgrounds · Rutube · OK.ru · VK / VK Видео · Facebook · Twitch (клипы) — **15 сервисов**.

> Некоторые сервисы (Reddit, Newgrounds, OK.ru) блокируют анонимные запросы с
> датацентровых/VPN-адресов; Instagram и Facebook для полного доступа могут требовать
> cookies. Подробнее — в разделе «Ограничения».

## Установка

Пакета нет в PyPI — ставится из исходников или из Git:

```bash
# из репозитория
pip install git+https://github.com/USER/ember.git

# для поддержки --cookies-from-browser обязательно
pip install yt-dlp
```

После установки доступны и Python-API (`import ember`), и команда `ember`.

Для скачивания HLS с раздельными дорожками, склейки видео+аудио, встраивания
метаданных и режима «только аудио» нужен **ffmpeg** в `PATH` (для прямых mp4 и
обычного HLS не требуется).

## Быстрый старт — Python

```python
import ember

# 1) получить прямые ссылки и метаданные
result = ember.extract("https://vimeo.com/76979871")
print(result.title, result.author, result.thumbnail)
for m in result.media:
    print(m.kind, m.quality, m.url)

# 2) скачать средствами Ember (yt-dlp не нужен)
def on_progress(p: ember.DownloadProgress):
    if p.fraction is not None:
        print(f"{p.fraction*100:.0f}%")

paths = ember.download(
    result, "downloads/",
    max_height=720,        # ограничить качество
    concurrency=6,         # параллельные сегменты HLS
    on_progress=on_progress,
    embed_metadata=True,   # вписать название/автора (ffmpeg)
)
```

Проверка ссылки и список качеств:

```python
ember.can_extract(url)                 # поддерживается ли ссылка
ember.available_qualities(result.media[0])   # напр. [1080, 720, 480]
ember.ffmpeg_available()               # есть ли ffmpeg
```

Плейлисты (пока SoundCloud sets):

```python
if ember.supports_playlist(url):
    for entry in ember.extract_playlist(url).entries:
        ember.download(entry, "downloads/")
```

## Быстрый старт — командная строка

Ссылку берите в кавычках. **Без `-d` команда только показывает ссылки и метаданные —
загрузка не начинается.** Позиция флагов не важна. Текст справки и ошибок — на английском.

```bash
# показать прямые ссылки и метаданные
ember "https://x.com/user/status/123456789"

# то же в JSON
ember "https://www.tiktok.com/@user/video/7123456789" --json

# СКАЧАТЬ: -d включает загрузку (имя с сайта, папка — текущая)
ember -d "https://vimeo.com/76979871"

# своё имя файла (-o) и папка (-p)
ember -d -o myclip -p downloads "https://vimeo.com/76979871"

# ограничить качество, качать HLS в 6 потоков
ember -d -p downloads --max-height 720 --concurrency 6 "https://rutube.ru/video/<id>/"

# только аудио + метаданные в файл (нужен ffmpeg)
ember -d --audio-only --embed-metadata "https://soundcloud.com/user/track"

# плейлист/набор целиком
ember -d --playlist "https://soundcloud.com/user/sets/name"

# cookies (NSFW-твиты, закрытый Instagram)
ember "https://x.com/user/status/123" --cookies "auth_token=...; ct0=..."
ember "https://x.com/user/status/123" --cookies-from-browser firefox
```

## Шпаргалка по ключам

| Ключ | Значение |
|---|---|
| `-d`, `--download` | включить скачивание (без него — только показать ссылки) |
| `-o`, `--output NAME` | имя файла без расширения (по умолчанию — с сайта); включает загрузку |
| `-p`, `--path DIR` | папка назначения (по умолчанию — текущая); включает загрузку |
| `--json` | вывести метаданные в JSON |
| `--max-height N` | ограничить качество по высоте (напр. `720`) |
| `--audio-only` | сохранить только звук (нужен ffmpeg); включает загрузку |
| `--concurrency N` | параллельных сегментов HLS (по умолчанию `1`) |
| `--embed-metadata` (`--metadata`) | вписать название/автора в файл (нужен ffmpeg); включает загрузку |
| `--playlist` | обработать как набор (SoundCloud sets) |
| `--timeout SEC` | таймаут запроса, сек (по умолчанию `15`) |
| `--cookies "a=1; b=2"` | cookies строкой |
| `--cookies-file FILE` | cookies.txt в формате Netscape (как у yt-dlp) |
| `--cookies-from-browser B` | cookies из браузера: brave/chrome/chromium/edge/firefox/opera/safari/vivaldi/whale |
| `--browser-profile P` | профиль браузера для предыдущего ключа |
| `-h`, `--help` | показать справку и выйти |

Справка в терминале — `ember --help` или `ember -h`:

```
usage: ember [-h] [--json] [--timeout SEC] [-d] [-o NAME] [-p DIR]
             [--max-height N] [--audio-only] [--concurrency N]
             [--embed-metadata] [--playlist] [--cookies "name=value; ..."]
             [--cookies-file cookies.txt] [--cookies-from-browser BROWSER]
             [--browser-profile PROFILE]
             url
```

## Как это работает

Ember обращается к тем же «внутренним» API сервисов, что и сайт в браузере, вытаскивает
из ответа прямые ссылки на медиа и метаданные и складывает их в объект `Result`. Каждый
сервис — отдельный небольшой модуль в `ember/services/`. HLS-манифесты разбираются
собственным парсером, сегменты качаются и собираются в файл (при наличии ffmpeg —
ремукс в `.mp4`).

## Ограничения

- **Instagram** — анонимно часто отдаёт только превью; для полного качества передайте cookies.
- **Reddit, Newgrounds, OK.ru** — блокируют анонимные запросы с датацентровых/VPN-IP;
  на обычном домашнем IP работают. Помогает `proxies=`/другой IP.
- **Facebook** — публичное видео обычно требует cookies.
- **Twitter/X** — NSFW-твиты требуют cookies аккаунта (`auth_token` и `ct0`).
- Полноценный YouTube не входит в задачи Ember — используйте для него yt-dlp.

## Благодарности

Методы извлечения ориентированы на подходы проекта
[imputnet/cobalt](https://github.com/imputnet/cobalt).
