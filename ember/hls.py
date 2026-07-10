"""Minimal HLS (m3u8) playlist parsing without third-party dependencies.

Lets Ember pick quality and download HLS video itself, without relying on
yt-dlp. Understands the master playlist (quality list) and the media
playlist (segment list + init segment for fMP4).
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
    """One quality variant from the master playlist."""
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
        """Best variant; with max_height, the best at or below it."""
        if not self.variants:
            return None
        if max_height:
            capped = [v for v in self.variants if (v.height or 0) <= max_height]
            if capped:
                return max(capped, key=lambda v: (v.height or 0, v.bandwidth))
            # ничего <= cap: берём наименьший из имеющихся, а не наибольший
            return min(self.variants, key=lambda v: (v.height or 0, v.bandwidth))
        return max(self.variants, key=lambda v: (v.height or 0, v.bandwidth))

    def audio_url_for(self, variant: HlsVariant) -> Optional[str]:
        """URL of the separate audio track for a variant (if any)."""
        tracks = self.audio.get(variant.audio_group or "")
        if not tracks:
            return None
        default = next((t for t in tracks if t.get("default")), tracks[0])
        return default.get("uri")


def parse_master(text: str, base_url: str) -> HlsMaster:
    """Parse a master playlist. If it is actually a media playlist (has
    segments), returns an empty master — the caller detects that."""
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
    """Media playlist: init segment (for fMP4) + segment list + encryption."""
    init_url: Optional[str] = None
    segments: List[str] = field(default_factory=list)
    is_fmp4: bool = False
    key_method: Optional[str] = None      # "AES-128" | None
    key_uri: Optional[str] = None
    key_iv: Optional[bytes] = None        # явный IV или None (тогда IV = номер сегмента)
    media_sequence: int = 0
    is_live: bool = False


def parse_media(text: str, base_url: str) -> HlsMedia:
    media = HlsMedia()
    has_endlist = False
    playlist_type = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                media.media_sequence = int(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            playlist_type = line.split(":", 1)[1].strip().upper()
        elif line.startswith("#EXT-X-ENDLIST"):
            has_endlist = True
        elif line.startswith("#EXT-X-KEY:"):
            attrs = _parse_attrs(line)
            method = attrs.get("METHOD")
            if method == "NONE":
                media.key_method = media.key_uri = media.key_iv = None
            elif method:
                media.key_method = method
                media.key_uri = urljoin(base_url, attrs["URI"]) if attrs.get("URI") else None
                iv = attrs.get("IV")
                media.key_iv = (bytes.fromhex(iv[2:]) if iv and iv[:2].lower() == "0x"
                                else None)
        elif line.startswith("#EXT-X-MAP:"):
            attrs = _parse_attrs(line)
            if attrs.get("URI"):
                media.init_url = urljoin(base_url, attrs["URI"])
                media.is_fmp4 = True
        elif not line.startswith("#"):
            media.segments.append(urljoin(base_url, line))
    # только явный live-сигнал; VOD без ENDLIST (бывает у CDN) не блокируем
    # ponytail: EVENT-only; типизированный live без метки скачается частично
    media.is_live = playlist_type == "EVENT" and not has_endlist
    return media


def looks_like_media_playlist(text: str) -> bool:
    return "#EXTINF" in text or "#EXT-X-MAP" in text
