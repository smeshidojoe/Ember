"""Download an Ember result without yt-dlp.

Features:
  - direct files (mp4/mp3/jpg…) with resume (HTTP Range);
  - HLS: manifest parsing, quality selection, parallel segment download;
  - HLS assembly without ffmpeg (single stream) or muxing via ffmpeg;
  - progress callback, metadata embedding, audio-only mode.

ffmpeg is looked up on PATH; without it, complex cases are saved in their
native container (.ts) or report that ffmpeg is required.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from . import hls
from ._browser_cookies import _pkcs7_unpad
from ._browser_cookies import aes_cbc_decrypt as _aes_cbc_decrypt
from .errors import ExtractionError, NetworkError
from .http import Context, make_context
from .models import Media, Result, safe_filename

log = logging.getLogger(__name__)


@dataclass
class DownloadProgress:
    """Progress state passed to the on_progress callback."""
    downloaded: int = 0                      # bytes downloaded
    total: Optional[int] = None              # total bytes (if known)
    segments_done: int = 0                   # HLS segments downloaded
    segments_total: Optional[int] = None     # total HLS segments
    stage: str = "download"                  # "download" | "mux" | "metadata"
    path: Optional[str] = None

    @property
    def fraction(self) -> Optional[float]:
        if self.total:
            return min(1.0, self.downloaded / self.total)
        if self.segments_total:
            return min(1.0, self.segments_done / self.segments_total)
        return None


ProgressCb = Callable[[DownloadProgress], None]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# ----------------------------------------------------------------------------
# прямые файлы
# ----------------------------------------------------------------------------

def _stream_to_file(ctx: Context, media: Media, out: Path,
                    on_progress: Optional[ProgressCb], resume: bool) -> None:
    part = out.with_suffix(out.suffix + ".part")
    headers = dict(media.http_headers or {})
    mode = "wb"
    existing = 0
    if resume and part.exists():
        existing = part.stat().st_size
        if existing:
            headers["Range"] = f"bytes={existing}-"
            mode = "ab"

    r = ctx.get(media.url, headers=headers or None, stream=True)
    if r.status_code == 200 and mode == "ab":
        # сервер проигнорировал Range — начинаем заново
        existing, mode = 0, "wb"
    elif r.status_code not in (200, 206):
        raise NetworkError(f"HTTP {r.status_code} while downloading {media.url[:80]}")

    total = None
    clen = r.headers.get("Content-Length")
    if clen:
        total = int(clen) + existing
    prog = DownloadProgress(downloaded=existing, total=total, path=str(out))

    with open(part, mode) as f:
        for chunk in r.iter_content(65536):
            if not chunk:
                continue
            f.write(chunk)
            prog.downloaded += len(chunk)
            if on_progress:
                on_progress(prog)
    os.replace(part, out)


# ----------------------------------------------------------------------------
# HLS
# ----------------------------------------------------------------------------

def _assemble_hls(ctx: Context, playlist_url: str, headers: Optional[dict],
                  out: Path, *, concurrency: int,
                  on_progress: Optional[ProgressCb],
                  prog: Optional[DownloadProgress]) -> bool:
    """Download a media playlist (init + segments) into one file.
    Returns True if fMP4 (otherwise MPEG-TS)."""
    text = ctx.get(playlist_url, headers=headers or None).text
    media = hls.parse_media(text, playlist_url)
    if media.is_live:
        raise ExtractionError("live HLS streams are not supported", "hls")
    if not media.segments:
        raise ExtractionError("HLS playlist has no segments", "hls")
    if prog is not None:
        prog.segments_total = len(media.segments)

    # шифрование сегментов
    aes_key = None
    if media.key_method == "AES-128" and media.key_uri:
        aes_key = ctx.get(media.key_uri, headers=headers or None).content
    elif media.key_method and media.key_method != "AES-128":
        raise ExtractionError(f"unsupported HLS encryption: {media.key_method}", "hls")

    def fetch(url: str) -> bytes:
        r = ctx.get(url, headers=headers or None)
        if r.status_code != 200:
            raise NetworkError(f"HTTP {r.status_code} on an HLS segment")
        return r.content

    def decrypt(idx: int, data: bytes) -> bytes:
        if not aes_key:
            return data
        iv = media.key_iv or (media.media_sequence + idx).to_bytes(16, "big")
        return _pkcs7_unpad(_aes_cbc_decrypt(aes_key, iv, data))

    def emit(data: bytes):
        if prog is not None:
            prog.segments_done += 1
            prog.downloaded += len(data)
            if on_progress:
                on_progress(prog)

    with open(out, "wb") as f:
        if media.init_url:
            f.write(fetch(media.init_url))
        if concurrency > 1:
            # качаем параллельно, расшифровываем и пишем строго по порядку
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                for idx, data in enumerate(pool.map(fetch, media.segments)):
                    data = decrypt(idx, data)
                    f.write(data)
                    emit(data)
        else:
            for idx, seg in enumerate(media.segments):
                data = decrypt(idx, fetch(seg))
                f.write(data)
                emit(data)
    return media.is_fmp4


# ----------------------------------------------------------------------------
# ffmpeg
# ----------------------------------------------------------------------------

def _run_ffmpeg(args: List[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", *args],
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise ExtractionError(
            "ffmpeg failed: "
            + proc.stderr.decode("utf-8", "replace")[-300:], "download")


def _meta_args(meta: Optional[dict]) -> List[str]:
    args: List[str] = []
    for key in ("title", "artist"):
        if meta and meta.get(key):
            args += ["-metadata", f"{key}={meta[key]}"]
    return args


def _remux(src: Path, out: Path, meta: Optional[dict] = None) -> None:
    _run_ffmpeg(["-i", str(src), "-c", "copy", *_meta_args(meta), str(out)])


def _mux(video: Path, audio: Path, out: Path, meta: Optional[dict] = None) -> None:
    _run_ffmpeg(["-i", str(video), "-i", str(audio), "-c", "copy",
                 "-map", "0:v:0", "-map", "1:a:0", *_meta_args(meta), str(out)])


def _embed_metadata(path: Path, meta: dict) -> None:
    """Write metadata into a finished file (by remuxing the container)."""
    tmp = path.with_suffix(".meta" + path.suffix)
    _run_ffmpeg(["-i", str(path), "-c", "copy", *_meta_args(meta), str(tmp)])
    os.replace(tmp, path)


def _to_audio(path: Path, out: Path) -> None:
    """Extract the audio track to mp3 (for audio-only mode)."""
    _run_ffmpeg(["-i", str(path), "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(out)])


# ----------------------------------------------------------------------------
# публичный API
# ----------------------------------------------------------------------------

def _pick_progressive_url(media: Media, max_height: Optional[int]) -> str:
    """Pick the URL of the wanted quality from variants (or best media.url)."""
    if not media.variants or not max_height:
        return media.url
    ok = [v for v in media.variants if (v.height or 0) <= max_height]
    pool = ok or media.variants
    best = max(pool, key=lambda v: v.height or 0)
    return best.url


def available_qualities(media: Media, ctx: Optional[Context] = None) -> List[int]:
    """List of available heights (e.g. [1080, 720, 480]) for quality choice."""
    if media.ext != "m3u8":
        heights = {v.height for v in media.variants if v.height}
        if media.quality and media.quality.rstrip("p").isdigit():
            heights.add(int(media.quality.rstrip("p")))
        return sorted(heights, reverse=True)
    ctx = ctx or make_context()
    text = ctx.get(media.url, headers=media.http_headers or None).text
    if hls.looks_like_media_playlist(text):
        return []
    master = hls.parse_master(text, media.url)
    return sorted({v.height for v in master.variants if v.height}, reverse=True)


def download_media(media: Media, out_path: str, *,
                   ctx: Optional[Context] = None,
                   max_height: Optional[int] = None,
                   concurrency: int = 1,
                   on_progress: Optional[ProgressCb] = None,
                   resume: bool = True,
                   audio_only: bool = False,
                   meta: Optional[dict] = None) -> str:
    """Download one Media. Returns the ACTUAL file path."""
    ctx = ctx or make_context()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    log.info("download %s -> %s", media.ext, out)

    if media.ext != "m3u8":
        chosen = Media(kind=media.kind, url=_pick_progressive_url(media, max_height),
                       ext=media.ext, http_headers=media.http_headers)
        _stream_to_file(ctx, chosen, out, on_progress, resume)
        result_path = out
    else:
        result_path = _download_hls(ctx, media, out, max_height=max_height,
                                    concurrency=concurrency, on_progress=on_progress,
                                    meta=meta)

    # метаданные для прямых файлов (для HLS уже вшиты при remux/mux)
    if meta and media.ext != "m3u8" and ffmpeg_available():
        if on_progress:
            on_progress(DownloadProgress(stage="metadata", path=str(result_path)))
        _embed_metadata(result_path, meta)

    if audio_only and ffmpeg_available() and media.kind == "video":
        audio_out = Path(result_path).with_suffix(".mp3")
        _to_audio(Path(result_path), audio_out)
        os.remove(result_path)
        result_path = audio_out

    return str(result_path)


def _download_hls(ctx: Context, media: Media, out: Path, *,
                  max_height: Optional[int], concurrency: int,
                  on_progress: Optional[ProgressCb], meta: Optional[dict]) -> Path:
    master_text = ctx.get(media.url, headers=media.http_headers or None).text
    if hls.looks_like_media_playlist(master_text):
        variant_url, audio_url = media.url, None
    else:
        master = hls.parse_master(master_text, media.url)
        variant = master.best(max_height=max_height)
        if not variant:
            raise ExtractionError("HLS master has no quality variants", "download")
        variant_url = variant.url
        audio_url = master.audio_url_for(variant)

    prog = DownloadProgress(path=str(out))
    with tempfile.TemporaryDirectory() as tmp:
        vpath = Path(tmp) / "v"
        is_fmp4 = _assemble_hls(ctx, variant_url, media.http_headers, vpath,
                                concurrency=concurrency, on_progress=on_progress,
                                prog=prog)
        if audio_url:
            if not ffmpeg_available():
                raise ExtractionError(
                    "this HLS video has a separate audio track — muxing needs "
                    "ffmpeg in PATH (or pass the m3u8 to yt-dlp)", "download")
            apath = Path(tmp) / "a"
            _assemble_hls(ctx, audio_url, media.http_headers, apath,
                          concurrency=concurrency, on_progress=None, prog=None)
            if on_progress:
                on_progress(DownloadProgress(stage="mux", path=str(out)))
            _mux(vpath, apath, out, meta)
            return out

        if ffmpeg_available():
            if on_progress:
                on_progress(DownloadProgress(stage="mux", path=str(out)))
            _remux(vpath, out, meta)
            return out
        final = out if is_fmp4 else out.with_suffix(".ts")
        shutil.copyfile(vpath, final)
        return final


def _download_subtitles(result: Result, out_dir: str, base: str,
                        ctx: Context) -> List[str]:
    paths = []
    for sub in result.subtitles:
        out = Path(out_dir) / f"{base}.{safe_filename(sub.lang)}.{sub.ext}"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            _stream_to_file(ctx, Media(kind="text", url=sub.url, ext=sub.ext),
                            out, None, False)
            paths.append(str(out))
        except (NetworkError, OSError) as e:
            log.warning("subtitle %s failed: %s", sub.lang, e)
    return paths


def download(result: Result, out_dir: str = ".", *,
             filename: Optional[str] = None,
             ctx: Optional[Context] = None,
             max_height: Optional[int] = None,
             concurrency: int = 1,
             on_progress: Optional[ProgressCb] = None,
             audio_only: bool = False,
             embed_metadata: bool = False,
             subtitles: bool = False) -> List[str]:
    """Download a whole Result. Returns paths of the created files.

    filename — base file name without extension; if omitted, taken from
    metadata (result.filename_hint, i.e. from the site).
    subtitles=True — also download subtitle tracks alongside.
    """
    ctx = ctx or make_context()
    base = safe_filename(filename) if filename else (result.filename_hint or "media")
    meta = None
    if embed_metadata:
        meta = {"title": result.title, "artist": result.author}

    written: List[str] = []
    if result.kind == "merge":
        if not ffmpeg_available():
            raise ExtractionError(
                "video and audio are separate — muxing needs ffmpeg in PATH",
                "download")
        video, audio = result.media[0], result.media[1]
        with tempfile.TemporaryDirectory() as tmp:
            vp = Path(tmp) / f"v.{video.ext}"
            ap = Path(tmp) / f"a.{audio.ext}"
            download_media(video, str(vp), ctx=ctx, max_height=max_height,
                           concurrency=concurrency, on_progress=on_progress)
            download_media(audio, str(ap), ctx=ctx)
            out = Path(out_dir) / f"{base}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            _mux(vp, ap, out, meta)
        written = [str(out)]
    else:
        multiple = len(result.media) > 1
        for i, media in enumerate(result.media, 1):
            suffix = f"_{i}" if multiple else ""
            ext = "mp4" if media.ext == "m3u8" else media.ext
            out = Path(out_dir) / f"{base}{suffix}.{ext}"
            written.append(download_media(
                media, str(out), ctx=ctx, max_height=max_height,
                concurrency=concurrency, on_progress=on_progress,
                audio_only=audio_only, meta=meta))

    if subtitles and result.subtitles:
        written += _download_subtitles(result, out_dir, base, ctx)
    return written
