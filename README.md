# Ember

Аналог [cobalt](https://github.com/imputnet/cobalt) в виде встраиваемой Python-библиотеки.
По ссылке на пост возвращает **прямые URL медиафайлов + метаданные** — скачивает уже ваша программа.

Поддерживаемые сервисы: **TikTok** (видео, фото-посты, музыка), **Twitter/X** (видео, гифки, фото), **Instagram** (посты, Reels, карусели), **Reddit** (видео, гифки, картинки, галереи).

Единственная зависимость — `requests`. Нужен Python 3.9+.

## Использование

```python
import ember

result = ember.extract("https://www.tiktok.com/@user/video/7123456789")

print(result.title, result.author)
for m in result.media:
    print(m.kind, m.url)          # прямая ссылка
    print(m.http_headers)         # передайте эти заголовки загрузчику!
```

### Что возвращается

`Result.kind` говорит, как обращаться с медиа:

| kind | значение |
|---|---|
| `single` | один файл — качаете `media[0].url` |
| `merge` | видео и аудио раздельно (Reddit) — скачать оба и объединить ffmpeg |
| `gallery` | несколько независимых файлов (карусель, фото-пост) |

`Result.filename_hint` — готовое безопасное имя файла без расширения.

**Важно:** у каждого `Media` есть `http_headers`. Для TikTok там cookies и User-Agent — без них CDN вернёт 403. Всегда передавайте их своему загрузчику (у yt-dlp это опция `http_headers`).

### Проверка ссылки и fallback на yt-dlp

```python
if ember.can_extract(url):
    try:
        result = ember.extract(url)
    except ember.EmberError:
        run_ytdlp(url)   # ваш существующий путь
else:
    run_ytdlp(url)
```

### Дополнительные параметры

```python
ember.extract(
    url,
    timeout=20,                                # сек на каждый запрос
    proxies={"https": "http://127.0.0.1:8080"},
    cookies={"sessionid": "..."},              # для Instagram под логином
)
```

## Использование в cmd

Открываете обычный **cmd** (в любой папке — пакет установлен в систему) и пишете:

```
python -m ember "ССЫЛКА"
```

Ссылку **обязательно берите в кавычки** — иначе cmd обрежет её на символах `&` и `?`.

Примеры:

```
:: показать прямые ссылки и метаданные
python -m ember "https://x.com/user/status/123456789"

:: то же, но в виде JSON (удобно, если разбираете вывод программой)
python -m ember "https://www.tiktok.com/@user/video/7123456789" --json

:: увеличить таймаут (медленный интернет/прокси)
python -m ember "https://redd.it/abc123" --timeout 30

:: с cookies (для NSFW-твитов и закрытого Instagram)
python -m ember "https://x.com/user/status/123" --cookies "auth_token=...; ct0=..."
python -m ember "https://x.com/user/status/123" --cookies-from-browser firefox
python -m ember "https://x.com/user/status/123" --cookies-file cookies.txt
```

Все ключи разом — `python -m ember --help`.

Что выведет (пример):

```
сервис:   twitter
тип:      single
автор:    MKBHD
название: Gaming phones… please never change
файл:     twitter_MKBHD_1858993800800334259
  1. video .mp4
     https://video.twimg.com/amplify_video/.../vid.mp4
```

Строка после `1.` — это и есть прямая ссылка, её можно вставить в браузер/загрузчик.

## Как работает Ember (кратко)

Ember **не качает файлы** — он выясняет, где реальный файл лежит, и отдаёт на него прямую
ссылку. Принцип тот же, что у cobalt:

1. **Определяет сервис по ссылке.** Роутер сверяет URL с шаблонами каждого сервиса
   (`can_extract` → какой из TikTok/Twitter/Instagram/Reddit). Не подошло ни к одному —
   `UnsupportedUrlError`, и вы отдаёте ссылку своему yt-dlp.
2. **Ходит на «внутренние» API сервиса — так же, как это делает сам сайт в браузере.**
   Для каждого сервиса свой приём: TikTok — читает JSON, встроенный в HTML страницы видео;
   Twitter/X — дёргает публичный API твитов; Instagram — GraphQL / embed / мобильный oembed
   по очереди; Reddit — открытый `.json`-эндпоинт поста. Никакого «взлома» — только те же
   запросы, что уходят при обычном просмотре.
3. **Достаёт из ответа прямые URL медиа и метаданные** (автор, заголовок, качество) и
   складывает в объект `Result` со списком `Media`. Если у сервиса видео и звук раздельно
   (Reddit), помечает `kind="merge"` — их надо смуксить (это умеет ffmpeg / yt-dlp).
4. **Возвращает ссылки вам.** К каждой ссылке прилагаются `http_headers` (cookies, User-Agent) —
   некоторые CDN (например TikTok) без них отвечают 403. Скачивает уже ваша программа
   любым способом.

Почему так удобнее для встраивания: библиотека делает только «умную» часть (разбор страницы),
не тянет за собой скачивание, ffmpeg и тяжёлые зависимости — кроме `requests` ничего не нужно.
Каждый сервис — отдельный файл в `ember/services/`; если сайт поменяет формат, чинится точечно
один файл, остальное не затрагивается.

### Как передать cookies

Три способа, от простого к ручному:

```
# 1. Прямо из браузера (как yt-dlp --cookies-from-browser)
python -m ember "<ссылка>" --cookies-from-browser firefox

# 2. Файл cookies.txt (формат Netscape — его экспортируют расширения браузера)
python -m ember "<ссылка>" --cookies-file cookies.txt

# 3. Вручную, две cookie строкой
python -m ember "<ссылка>" --cookies "auth_token=...; ct0=..."
```

В коде: `extract(url, cookies_from_browser="firefox")` или
`extract(url, cookies={"auth_token": "...", "ct0": "..."})`.

**Важно про Chrome/Edge/Brave на Windows:** начиная с Chrome 127 cookies
шифруются App-Bound Encryption, и их не может прочитать даже yt-dlp
([yt-dlp#10927](https://github.com/yt-dlp/yt-dlp/issues/10927)). Поэтому
`--cookies-from-browser chrome` там не сработает. Рабочие варианты:
**Firefox** (шифрование другое, читается), либо экспорт **cookies.txt**
расширением, либо ручной ввод двух cookie. Ember выдаёт понятную подсказку,
если расшифровка не удалась.
- Сервисы меняют форматы страниц. Каждый извлекатель — отдельный файл в
  `ember/services/`, чинится точечно. При обновлении сверяйтесь с
  [исходниками cobalt](https://github.com/imputnet/cobalt/tree/main/api/src/processing/services) —
  архитектура намеренно повторяет их подходы.
- Ошибки: ловите базовый `ember.EmberError` (подклассы: `UnsupportedUrlError`,
  `NetworkError`, `ExtractionError`).
