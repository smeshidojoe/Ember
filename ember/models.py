"""Модели данных: результат извлечения, медиафайлы, плейлисты."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MediaVariant:
    """Альтернативное качество того же медиа (для выбора качества)."""

    url: str
    height: Optional[int] = None
    quality: Optional[str] = None
    ext: str = "mp4"


@dataclass
class Media:
    """Один медиафайл (или поток), готовый к скачиванию.

    http_headers обязательно передавайте своему загрузчику — часть CDN
    (например, TikTok) отдаёт файл только с теми же cookies/User-Agent,
    с которыми была открыта страница.

    variants — другие доступные качества (если сервис их отдаёт). Верхний
    url всегда указывает на лучшее качество; variants позволяют выбрать ниже.
    """

    kind: str                # "video" | "audio" | "photo" | "gif"
    url: str
    ext: str = "mp4"
    quality: Optional[str] = None          # например "1080p", "128kbps"
    http_headers: Dict[str, str] = field(default_factory=dict)
    variants: List[MediaVariant] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "url": self.url,
            "ext": self.ext,
            "quality": self.quality,
            "http_headers": self.http_headers,
            "variants": [
                {"url": v.url, "height": v.height,
                 "quality": v.quality, "ext": v.ext}
                for v in self.variants
            ],
        }


@dataclass
class Result:
    """Результат ember.extract().

    kind:
      "single"  — один файл, media[0];
      "merge"   — раздельные видео + аудио, их нужно смуксить (ffmpeg);
      "gallery" — несколько независимых файлов (карусель фото и т.п.).
    """

    service: str
    kind: str
    media: List[Media]
    title: Optional[str] = None
    author: Optional[str] = None
    source_url: str = ""
    filename_hint: Optional[str] = None    # безопасное имя файла без расширения
    thumbnail: Optional[str] = None        # URL обложки/превью, если есть

    @property
    def requires_merge(self) -> bool:
        return self.kind == "merge"

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "kind": self.kind,
            "title": self.title,
            "author": self.author,
            "source_url": self.source_url,
            "filename_hint": self.filename_hint,
            "thumbnail": self.thumbnail,
            "media": [m.to_dict() for m in self.media],
        }


@dataclass
class Playlist:
    """Несколько постов/треков по одной ссылке (плейлист, набор, лента)."""

    service: str
    entries: List[Result]
    title: Optional[str] = None
    author: Optional[str] = None
    source_url: str = ""

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "title": self.title,
            "author": self.author,
            "source_url": self.source_url,
            "entries": [r.to_dict() for r in self.entries],
        }


_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(text: str, max_len: int = 120) -> str:
    """Превращает произвольный текст в безопасное имя файла (без расширения)."""
    text = _FILENAME_BAD.sub("_", text).strip(" ._")
    text = re.sub(r"\s+", " ", text)
    return text[:max_len] if text else "media"
