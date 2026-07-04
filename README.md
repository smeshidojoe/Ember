# Ember

Аналог [cobalt](https://github.com/imputnet/cobalt) в виде встраиваемой Python-библиотеки.
По ссылке на пост возвращает **прямые URL медиафайлов + метаданные** — скачивает уже ваша программа.

Поддерживаемые сервисы: **TikTok** (видео, фото-посты, музыка), **Twitter/X** (видео, гифки, фото), **Instagram** (посты, Reels, карусели), **Reddit** (видео, гифки, картинки, галереи).

Единственная зависимость — `requests`. Нужен Python 3.9+.

## Установка

Пакета нет в публичном реестре PyPI, поэтому `pip install ember` **не сработает**.
Ставится он одним из способов ниже (все они всё равно используют `pip`, просто
указывают, откуда брать код). Проверить, что установилось: `python -c "import ember; print(ember.__version__)"`.

### Вариант 1 — из локальной папки (сейчас так и сделано)

```
pip install -e D:\Projects\Ember
```

`-e` (editable) — пакет ссылается на папку, правки в коде подхватываются сразу,
переустанавливать не нужно. Минус: **папку нельзя удалять или переносить**, иначе
импорт сломается. Если хотите «жёсткую» установку, скопированную внутрь Python
(папку потом можно удалить) — уберите `-e`:

```
pip install D:\Projects\Ember
```

### Вариант 2 — из Git (если выложите репозиторий)

После загрузки на GitHub/GitLab ставится прямо по ссылке, скачивать вручную не надо:

```
pip install git+https://github.com/ВАШ_ЛОГИН/ember.git
```

Можно закрепить ветку или тег: `...ember.git@main` или `...ember.git@v0.1.0`.
Обновление до свежей версии из репозитория:

```
pip install --upgrade --force-reinstall git+https://github.com/ВАШ_ЛОГИН/ember.git
```

Чтобы этот способ работал, в репозитории должны лежать `pyproject.toml` и папка
`ember/` (они уже есть). На машине пользователя нужен установленный `git`.

Чтобы Ember ставился как зависимость вашей программы, добавьте ту же строку в её
`requirements.txt`:

```
git+https://github.com/ВАШ_ЛОГИН/ember.git
```

### Вариант 3 — вообще без установки

Просто скопируйте папку `ember/` рядом с кодом вашей программы (в тот же каталог,
где лежит главный `.py`). Тогда `import ember` заработает без всякого `pip`.
Единственное требование — чтобы был установлен `requests` (`pip install requests`).

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

## Интеграция в PySide6

Полный пример — [examples/pyside6_integration.py](examples/pyside6_integration.py):
извлечение в `QThreadPool` (не блокирует UI), скачивание через уже имеющийся yt-dlp,
автоматический fallback на yt-dlp, если Ember не справился.

## Ограничения и сопровождение

- **Instagram** — самый хрупкий: анонимный доступ периодически блокируют.
  Библиотека пробует GraphQL → embed-страницу → мобильный oembed
  (последний отдаёт только превью-картинку, у неё `quality == "thumbnail"`).
  Для полного качества на заблокированных сетях передайте cookies
  залогиненного аккаунта.
- **Reddit** блокирует анонимные запросы с VPN и хостинговых IP
  («blocked due to a network policy»). В этом случае библиотека выдаёт
  понятную `ExtractionError`; помогает `proxies=` с обычным IP.
- **Twitter/X**: NSFW-твиты без авторизации не отдаются (ограничение самого API).
  Решение — cookies аккаунта X (нужны две: `auth_token` и `ct0`).

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
