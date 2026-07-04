"""Простой кэш на диск с TTL — для значений, которые дорого получать
каждый запуск (client_id SoundCloud, анонимный токен VK).

Один JSON-файл в пользовательском каталоге кэша. Без зависимостей,
безопасно к битому файлу (просто игнорируется).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional


def _cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")  # Windows
    if base:
        d = Path(base) / "ember"
    else:
        d = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ember"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d / "cache.json"
    except OSError:
        return Path(tempfile.gettempdir()) / "ember_cache.json"


def _load() -> dict:
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        path = _cache_path()
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass  # кэш не критичен — молча пропускаем


def get(key: str) -> Optional[Any]:
    entry = _load().get(key)
    if not entry:
        return None
    if entry.get("expires", 0) < time.time():
        return None
    return entry.get("value")


def set(key: str, value: Any, ttl: float) -> None:
    data = _load()
    data[key] = {"value": value, "expires": time.time() + ttl}
    _save(data)


def invalidate(key: str) -> None:
    data = _load()
    if key in data:
        del data[key]
        _save(data)


def get_or_set(key: str, ttl: float, producer: Callable[[], Any]) -> Any:
    """Вернуть закэшированное значение или вычислить, сохранить и вернуть."""
    cached = get(key)
    if cached is not None:
        return cached
    value = producer()
    set(key, value, ttl)
    return value
