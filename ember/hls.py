"""Минимальный разбор HLS-плейлистов (m3u8) без сторонних зависимостей.

Нужен, чтобы Ember мог сам выбирать качество и скачивать HLS-видео,
не полагаясь на yt-dlp. Понимает master-плейлист (список качеств) и
media-плейлист (список сегментов + init-сегмент для fMP4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _parse_attrs(line: str) -> Dict[str, str]:
    attrs = {}
    for key, val in _ATTR_RE.findall(line):
        attrs[key] = val.strip('"')
    return attrs


@dataclass
class HlsVariant:
    """Один вариант качества из master-плейлиста."""
    url: str
    bandwidth: int = 0
    height: Optional[int] = None
    codecs: str = ""
    audio_group: Optional[str] = None


@dataclass
class HlsMaster:
    variants: List[HlsVariant] = field(default_factory=list)
    # group_id -> список аудиодорожек {"uri":..., "default":bool, "name":...}
    audio: Dict[str, List[dict]] = field(default_factory=dict)

    def best(self, max_height: Optional[int] = None) -> Optional[HlsVariant]:
        """Лучший вариант; при max_height — лучший из не выше указанного."""
        if not self.variants:
            return None
        pool = self.variants
        if max_height:
            capped = [v for v in pool if (v.height or 0) <= max_height]
            pool = capped or pool
        return max(pool, key=lambda v: (v.height or 0, v.bandwidth))

    def audio_url_for(self, variant: HlsVariant) -> Optional[str]:
        """URL отдельной аудиодорожки для варианта (если аудио вынесено)."""
        tracks = self.audio.get(variant.audio_group or "")
        if not tracks:
            return None
        default = next((t for t in tracks if t.get("default")), tracks[0])
        return default.get("uri")


def parse_master(text: str, base_url: str) -> HlsMaster:
    """Разбирает master-плейлист. Если это media-плейлист (есть сегменты),
    возвращает пустой master — вызывающий код это распознаёт."""
    master = HlsMaster()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = _parse_attrs(line)
            if attrs.get("TYPE") == "AUDIO" and attrs.get("URI"):
                group = attrs.get("GROUP-ID", "")
                master.audio.setdefault(group, []).append({
                    "uri": urljoin(base_url, attrs["URI"]),
                    "default": attrs.get("DEFAULT", "NO") == "YES",
                    "name": attrs.get("NAME", ""),
                })
        elif line.startswith("#EXT-X-STREAM-INF:"):
            attrs = _parse_attrs(line)
            uri = lines[i + 1] if i + 1 < len(lines) else None
            if not uri or uri.startswith("#"):
                continue
            res = attrs.get("RESOLUTION", "")
            height = int(res.split("x")[1]) if "x" in res else None
            master.variants.append(HlsVariant(
                url=urljoin(base_url, uri),
                bandwidth=int(attrs.get("BANDWIDTH", 0) or 0),
                height=height,
                codecs=attrs.get("CODECS", ""),
                audio_group=attrs.get("AUDIO") or None,
            ))
    return master


@dataclass
class HlsMedia:
    """media-плейлист: init-сегмент (для fMP4) + список сегментов."""
    init_url: Optional[str] = None
    segments: List[str] = field(default_factory=list)
    is_fmp4: bool = False


def parse_media(text: str, base_url: str) -> HlsMedia:
    media = HlsMedia()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP:"):
            attrs = _parse_attrs(line)
            if attrs.get("URI"):
                media.init_url = urljoin(base_url, attrs["URI"])
                media.is_fmp4 = True
        elif not line.startswith("#"):
            media.segments.append(urljoin(base_url, line))
    return media


def looks_like_media_playlist(text: str) -> bool:
    return "#EXTINF" in text or "#EXT-X-MAP" in text
