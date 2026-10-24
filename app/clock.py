"""Источник текущего времени. Вынесен отдельно, чтобы в тестах подставлять фиксированное «сейчас»."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


class Clock:
    """Возвращает локальное время пользователя без tzinfo (все даты в БД хранятся в его поясе)."""

    def __init__(self, tz: ZoneInfo):
        self.tz = tz

    def now(self) -> datetime:
        return datetime.now(self.tz).replace(tzinfo=None, second=0, microsecond=0)


class FixedClock(Clock):
    def __init__(self, fixed: datetime):
        self.fixed = fixed

    def now(self) -> datetime:
        return self.fixed
