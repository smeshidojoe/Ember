"""Data models: extraction result, media files, playlists."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MediaVariant:
    """An alternative quality of the same media (for quality selection)."""

    url: str
    height: Optional[int] = None
    quality: Optional[str] = None
    ext: str = "mp4"


@dataclass
class Media:
    """One media file (or stream), ready to download.

    Always pass http_headers to your downloader — some CDNs (e.g. TikTok)
    only serve the file with the same cookies/User-Agent used to open the page.

    variants — other available qualities (when the service exposes them). The
    top-level url always points to the best quality; variants let you pick lower.
    """

    kind: str                # "video" | "audio" | "photo" | "gif"
    url: str
    ext: str = "mp4"
    quality: Optional[str] = None          # e.g. "1080p", "128kbps"
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
class Subtitle:
    """A subtitle track (usually webvtt)."""
    lang: str
    url: str
    ext: str = "vtt"


@dataclass
class Result:
    """Result of ember.extract().

    kind:
      "single"  — one file, media[0];
      "merge"   — separate video + audio, must be muxed (ffmpeg);
      "gallery" — several independent files (photo carousel, etc.).
    """

    service: str
    kind: str
    media: List[Media]
    title: Optional[str] = None
    author: Optional[str] = None
    source_url: str = ""
    filename_hint: Optional[str] = None    # safe file name without extension
    thumbnail: Optional[str] = None        # cover/preview URL, if any
    duration: Optional[float] = None       # seconds, if the service reports it
    timestamp: Optional[int] = None        # unix seconds of publication
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    subtitles: List[Subtitle] = field(default_factory=list)

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
            "duration": self.duration,
            "timestamp": self.timestamp,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "media": [m.to_dict() for m in self.media],
            "subtitles": [{"lang": s.lang, "url": s.url, "ext": s.ext}
                          for s in self.subtitles],
        }


@dataclass
class Playlist:
    """Several posts/tracks behind one link (playlist, set, feed)."""

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


def to_timestamp(v) -> Optional[int]:
    """Unix seconds from an int/float or an ISO-8601 string; else None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(text: str, max_len: int = 120) -> str:
    """Turn arbitrary text into a safe file name (without extension)."""
    text = _FILENAME_BAD.sub("_", text).strip(" ._")
    text = re.sub(r"\s+", " ", text)
    return text[:max_len] if text else "media"
