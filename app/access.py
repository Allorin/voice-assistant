"""Общий код доступа к стенду (ACCESS_CODE).

Код задаётся секретом на хостинге и сообщается проверяющим отдельно. Без него работают только страница,
статика, /health и ссылки подписки на календарь (календарь не умеет передавать заголовки);
все запросы к /api требуют заголовок X-Access-Code. Подбор ограничен: после MAX_FAILURES неверных
попыток с одного адреса за минуту — ответ 429. Локально код не задан, и ничего не меняется.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import deque


class AccessGuard:
    MAX_FAILURES = 10
    WINDOW_SECONDS = 60.0

    def __init__(self, code: str = "", clock=time.monotonic):
        self._code = code.encode("utf-8")
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._code)

    def check(self, client: str, supplied: str | None) -> int:
        """HTTP-статус: 200 — пропустить, 401 — нет или неверный код, 429 — слишком много неверных попыток."""
        if not self.enabled:
            return 200
        now = self._clock()
        with self._lock:
            recent = self._failures.setdefault(client, deque())
            while recent and now - recent[0] > self.WINDOW_SECONDS:
                recent.popleft()
            if len(recent) >= self.MAX_FAILURES:
                return 429
            if supplied and hmac.compare_digest(supplied.encode("utf-8"), self._code):
                recent.clear()
                return 200
            if supplied:  # пустой заголовок — это первый заход на страницу, а не попытка подбора
                recent.append(now)
            if not recent:
                self._failures.pop(client, None)
            return 401


def calendar_token(key: str, user: str) -> str:
    """Секрет в ссылке подписки на календарь: без ключа пусто (локальный запуск), с ключом — HMAC от профиля.
    Календари не передают заголовки, поэтому защита — неугадываемая ссылка, как у «секретного адреса» Google."""
    if not key:
        return ""
    return hmac.new(key.encode("utf-8"), user.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
