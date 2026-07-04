"""Пример внедрения Ember в PySide6-программу, которая уже использует yt-dlp.

Логика:
  1. Пользователь вставил ссылку.
  2. Если ember.can_extract(url) — пробуем Ember (быстро, без yt-dlp).
  3. Если Ember не справился или ссылка не его — fallback на yt-dlp.
  4. Всё выполняется в QThreadPool, чтобы не вешать интерфейс.

Скачивать полученные прямые ссылки можно чем угодно; здесь показан
вариант через тот же yt-dlp (он умеет и мержить видео+аудио через ffmpeg).
"""

import sys

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (QApplication, QLineEdit, QPushButton,
                               QTextEdit, QVBoxLayout, QWidget)

import ember


class ExtractSignals(QObject):
    finished = Signal(object)   # ember.Result
    failed = Signal(str)


class ExtractWorker(QRunnable):
    """Извлечение ссылок в фоновом потоке."""

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.signals = ExtractSignals()

    @Slot()
    def run(self):
        try:
            result = ember.extract(self.url, timeout=20)
            self.signals.finished.emit(result)
        except ember.EmberError as e:
            self.signals.failed.emit(str(e))


def download_with_ytdlp(result: "ember.Result", out_dir: str = "."):
    """Скачивание результата Ember через yt-dlp (у вас он уже есть).

    yt-dlp принимает прямые URL и http_headers, а для kind="merge"
    сам объединит видео и аудио через ffmpeg.
    """
    import yt_dlp

    if result.kind == "merge":
        video, audio = result.media[0], result.media[1]
        opts = {
            "outtmpl": f"{out_dir}/{result.filename_hint}.%(ext)s",
            "http_headers": video.http_headers or None,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            # прямые ссылки: видео + отдельное аудио
            info = {
                "id": result.filename_hint,
                "title": result.filename_hint,
                "formats": [
                    {"url": video.url, "format_id": "video", "ext": video.ext,
                     "vcodec": "h264", "acodec": "none"},
                    {"url": audio.url, "format_id": "audio", "ext": audio.ext,
                     "vcodec": "none", "acodec": "aac"},
                ],
                "format": "video+audio",
            }
            ydl.process_ie_result(info, download=True)
    else:
        for m in result.media:
            opts = {
                "outtmpl": f"{out_dir}/{result.filename_hint}.%(ext)s",
                "http_headers": m.http_headers or None,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([m.url])


class Demo(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ember demo")
        self.pool = QThreadPool.globalInstance()

        self.url_edit = QLineEdit(placeholderText="Вставьте ссылку…")
        self.button = QPushButton("Получить ссылки")
        self.log = QTextEdit(readOnly=True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.url_edit)
        layout.addWidget(self.button)
        layout.addWidget(self.log)

        self.button.clicked.connect(self.on_click)

    def on_click(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        if not ember.can_extract(url):
            self.log.append("Ссылка не для Ember — отдаём обычному yt-dlp.")
            # тут вызывайте ваш существующий путь через yt-dlp
            return
        self.log.append(f"Извлекаем: {url}")
        worker = ExtractWorker(url)
        worker.signals.finished.connect(self.on_done)
        worker.signals.failed.connect(self.on_fail)
        self.pool.start(worker)

    def on_done(self, result):
        self.log.append(f"[{result.service}] {result.title or result.filename_hint}")
        for m in result.media:
            self.log.append(f"  {m.kind}: {m.url[:120]}…")
        if result.requires_merge:
            self.log.append("  (видео и аудио раздельно — при скачивании нужен ffmpeg)")

    def on_fail(self, message):
        self.log.append(f"Ember не справился: {message}")
        self.log.append("Fallback: пробуем yt-dlp…")
        # тут вызывайте ваш существующий путь через yt-dlp


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = Demo()
    w.resize(700, 400)
    w.show()
    sys.exit(app.exec())
