"""Скачивание результата Ember без yt-dlp.

Возможности:
  - прямые файлы (mp4/mp3/jpg…) с докачкой (HTTP Range);
  - HLS: разбор манифеста, выбор качества, параллельная загрузка сегментов;
  - сборка HLS без ffmpeg (единый поток) или склейка через ffmpeg;
  - прогресс через колбэк, встраивание метаданных, режим «только аудио».

ffmpeg ищется в PATH; без него сложные случаи сохраняются в родном
контейнере (.ts) или сообщают, что ffmpeg нужен.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, List, Optional

from . import hls
from .errors import ExtractionError, NetworkError
from .http import Context, make_context
from .models import Media, Result, safe_filename


@dataclass
class DownloadProgress:
    """Состояние прогресса, передаётся в on_progress-колбэк."""
    downloaded: int = 0                      # скачано байт
    total: Optional[int] = None              # всего байт (если известно)
    segments_done: int = 0                   # сегментов HLS скачано
    segments_total: Optional[int] = None     # сегментов HLS всего
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
    """Скачивает media-плейлист (init + сегменты) в один файл.
    Возвращает True, если это fMP4 (иначе MPEG-TS)."""
    text = ctx.get(playlist_url, headers=headers or None).text
    media = hls.parse_media(text, playlist_url)
    if not media.segments:
        raise ExtractionError("HLS playlist has no segments", "hls")

    if prog is not None:
        prog.segments_total = len(media.segments)

    def fetch(url: str) -> bytes:
        r = ctx.get(url, headers=headers or None)
        if r.status_code != 200:
            raise NetworkError(f"HTTP {r.status_code} on an HLS segment")
        return r.content

    with open(out, "wb") as f:
        if media.init_url:
            f.write(fetch(media.init_url))

        if concurrency > 1:
            # качаем параллельно, пишем строго по порядку
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                for data in pool.map(fetch, media.segments):
                    f.write(data)
                    if prog is not None:
                        prog.segments_done += 1
                        prog.downloaded += len(data)
                        if on_progress:
                            on_progress(prog)
        else:
            for seg in media.segments:
                data = fetch(seg)
                f.write(data)
                if prog is not None:
                    prog.segments_done += 1
                    prog.downloaded += len(data)
                    if on_progress:
                        on_progress(prog)
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
    """Дописывает метаданные в готовый файл (перекладыванием контейнера)."""
    tmp = path.with_suffix(".meta" + path.suffix)
    _run_ffmpeg(["-i", str(path), "-c", "copy", *_meta_args(meta), str(tmp)])
    os.replace(tmp, path)


def _to_audio(path: Path, out: Path) -> None:
    """Извлекает аудиодорожку в mp3 (для режима «только аудио»)."""
    _run_ffmpeg(["-i", str(path), "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(out)])


# ----------------------------------------------------------------------------
# публичный API
# ----------------------------------------------------------------------------

def _pick_progressive_url(media: Media, max_height: Optional[int]) -> str:
    """Выбирает URL нужного качества из variants (или лучший media.url)."""
    if not media.variants or not max_height:
        return media.url
    ok = [v for v in media.variants if (v.height or 0) <= max_height]
    pool = ok or media.variants
    best = max(pool, key=lambda v: v.height or 0)
    return best.url


def available_qualities(media: Media, ctx: Optional[Context] = None) -> List[int]:
    """Список доступных высот (напр. [1080, 720, 480]) для выбора качества."""
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
    """Скачивает один Media. Возвращает ФАКТИЧЕСКИЙ путь к файлу."""
    ctx = ctx or make_context()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

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


def download(result: Result, out_dir: str = ".", *,
             filename: Optional[str] = None,
             ctx: Optional[Context] = None,
             max_height: Optional[int] = None,
             concurrency: int = 1,
             on_progress: Optional[ProgressCb] = None,
             audio_only: bool = False,
             embed_metadata: bool = False) -> List[str]:
    """Скачивает весь Result. Возвращает список путей к созданным файлам.

    filename — базовое имя файла без расширения; если не задано, берётся
    из метаданных (result.filename_hint, т.е. с сайта).
    """
    ctx = ctx or make_context()
    base = safe_filename(filename) if filename else (result.filename_hint or "media")
    meta = None
    if embed_metadata:
        meta = {"title": result.title, "artist": result.author}

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
        return [str(out)]

    multiple = len(result.media) > 1
    written: List[str] = []
    for i, media in enumerate(result.media, 1):
        suffix = f"_{i}" if multiple else ""
        ext = "mp4" if media.ext == "m3u8" else media.ext
        out = Path(out_dir) / f"{base}{suffix}.{ext}"
        written.append(download_media(
            media, str(out), ctx=ctx, max_height=max_height,
            concurrency=concurrency, on_progress=on_progress,
            audio_only=audio_only, meta=meta))
    return written
