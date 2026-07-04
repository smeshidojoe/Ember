"""Модели данных: результат извлечения и отдельные медиафайлы."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Media:
    """Один медиафайл (или поток), готовый к скачиванию.

    http_headers обязательно передавайте своему загрузчику — часть CDN
    (например, TikTok) отдаёт файл только с теми же cookies/User-Agent,
    с которыми была открыта страница.
    """

    kind: str                # "video" | "audio" | "photo" | "gif"
    url: str
    ext: str = "mp4"
    quality: Optional[str] = None          # например "1080p", "128kbps"
    http_headers: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "url": self.url,
            "ext": self.ext,
            "quality": self.quality,
            "http_headers": self.http_headers,
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
            "media": [m.to_dict() for m in self.media],
        }


_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(text: str, max_len: int = 120) -> str:
    """Превращает произвольный текст в безопасное имя файла (без расширения)."""
    text = _FILENAME_BAD.sub("_", text).strip(" ._")
    text = re.sub(r"\s+", " ", text)
    return text[:max_len] if text else "media"
