"""HTTP-обвязка: единая сессия, User-Agent, таймауты, обработка ошибок."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests

from .errors import NetworkError

# статусы, которые имеет смысл повторить (временные сбои)
_RETRY_STATUS = {429, 500, 502, 503, 504}

# Обычный десктопный Chrome — тот же подход, что у cobalt (genericUserAgent).
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class Context:
    """Контекст одного вызова extract(): сессия + таймаут."""

    session: requests.Session
    timeout: float = 15.0
    retries: int = 2          # число ПОВТОРОВ сверх первой попытки
    backoff: float = 0.6      # базовая пауза между повторами, секунды

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as e:
                last_exc = e
            else:
                # повторяем только временные серверные статусы
                if resp.status_code in _RETRY_STATUS and attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                return resp
            if attempt < self.retries:
                time.sleep(self.backoff * (attempt + 1))
        raise NetworkError(f"{method} {url}: {last_exc}")

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def head_ok(self, url: str, **kwargs) -> bool:
        """True, если по URL реально есть файл (для проверки аудиодорожек)."""
        try:
            r = self.request("HEAD", url, allow_redirects=True, **kwargs)
            return r.status_code == 200
        except NetworkError:
            return False

    def cookie_header(self, domain_part: str) -> str:
        """Собирает строку Cookie из cookies сессии для нужного домена."""
        pairs = []
        for c in self.session.cookies:
            if domain_part in (c.domain or ""):
                pairs.append(f"{c.name}={c.value}")
        return "; ".join(pairs)


def make_context(
    timeout: float = 15.0,
    proxies: Optional[dict] = None,
    session: Optional[requests.Session] = None,
    retries: int = 2,
) -> Context:
    if session is None:
        session = requests.Session()
    # у requests.Session всегда есть свой UA "python-requests/x.y" —
    # заменяем его, но не трогаем UA, заданный пользователем явно
    ua = session.headers.get("User-Agent", "")
    if not ua or ua.startswith("python-requests"):
        session.headers["User-Agent"] = DEFAULT_UA
    session.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    if proxies:
        session.proxies.update(proxies)
    return Context(session=session, timeout=timeout, retries=retries)
