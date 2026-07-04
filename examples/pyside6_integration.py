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
from PySide6.QtWidgets import (QApplication, QLineEdit, QProgressBar,
                               QPushButton, QTextEdit, QVBoxLayout, QWidget)

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


class DownloadSignals(QObject):
    progress = Signal(float)     # доля 0..1 (или -1, если неизвестна)
    finished = Signal(list)      # список путей
    failed = Signal(str)


class DownloadWorker(QRunnable):
    """Скачивание средствами самого Ember (yt-dlp не нужен), в фоне.

    Прогресс отдаётся сигналом, так что окно не подвисает и можно
    рисовать прогресс-бар. HLS качается в несколько потоков.
    """

    def __init__(self, result, out_dir="downloads", max_height=None):
        super().__init__()
        self.result, self.out_dir, self.max_height = result, out_dir, max_height
        self.signals = DownloadSignals()

    @Slot()
    def run(self):
        def on_progress(p: ember.DownloadProgress):
            self.signals.progress.emit(p.fraction if p.fraction is not None else -1.0)
        try:
            paths = ember.download(self.result, self.out_dir,
                                   max_height=self.max_height, concurrency=6,
                                   on_progress=on_progress)
            self.signals.finished.emit(paths)
        except ember.EmberError as e:
            self.signals.failed.emit(str(e))


class Demo(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ember demo")
        self.pool = QThreadPool.globalInstance()

        self.url_edit = QLineEdit(placeholderText="Вставьте ссылку…")
        self.button = QPushButton("Скачать")
        self.progress = QProgressBar()
        self.log = QTextEdit(readOnly=True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.url_edit)
        layout.addWidget(self.button)
        layout.addWidget(self.progress)
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
        # извлекли ссылки — сразу качаем средствами Ember с прогрессом
        dl = DownloadWorker(result, out_dir="downloads")
        dl.signals.progress.connect(self.on_progress)
        dl.signals.finished.connect(self.on_downloaded)
        dl.signals.failed.connect(self.on_fail)
        self.pool.start(dl)

    def on_progress(self, fraction: float):
        if fraction < 0:
            self.progress.setRange(0, 0)          # неизвестно — «бегущая» полоса
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(fraction * 100))

    def on_downloaded(self, paths):
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        for p in paths:
            self.log.append(f"готово: {p}")

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
