"""Иерархия ошибок Ember.

Хост-программе достаточно ловить EmberError: любая ошибка библиотеки —
её подкласс. Это удобно для fallback на yt-dlp.
"""


class EmberError(Exception):
    """Базовая ошибка Ember."""


class UnsupportedUrlError(EmberError):
    """Ссылка не относится ни к одному поддерживаемому сервису."""


class NetworkError(EmberError):
    """Сетевая ошибка: таймаут, обрыв соединения, HTTP 5xx и т.п."""


class ExtractionError(EmberError):
    """Сервис ответил, но извлечь медиа не удалось.

    Типичные причины: пост удалён/приватный, сервис изменил формат
    страницы, требуется авторизация (cookies).
    """

    def __init__(self, message: str, service: str = ""):
        super().__init__(message)
        self.service = service
