"""Состояние диалога: контекст («её» = последняя запись) и незавершённые действия (уточняющие вопросы)."""
from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time

from app.models import Item, ItemType
from app.recurrence import Recurrence


@dataclass
class Draft:
    """Черновик записи, которой не хватает данных (ждём ответа на уточняющий вопрос)."""
    type: ItemType
    title: str
    due: datetime | None = None
    has_time: bool = False
    has_date: bool = False
    deadline: bool = False
    duration: int | None = None
    conflicts_ok: bool = False
    recurrence: Recurrence | None = None   # правило повторения серии
    default_time: time | None = None       # «каждое утро» без точного времени
    collect_details: bool = False
    details_done: bool = False
    lead_done: bool = False
    notes: str = ""
    remind_before_min: int = 0


@dataclass
class Pending:
    kind: str            # slot — значение; choice — выбор из вариантов; confirm — да/нет; scope — одно или серия
    action: str          # create | update | delete | complete | reopen | search
    question: str
    slot: str | None = None
    draft: Draft | None = None
    candidates: list[int] = field(default_factory=list)
    command: object | None = None
    attempts: int = 0
    day: date | None = None   # scope: о каком повторении спросили
    text: str = ""            # исходная фраза (нужна, чтобы после уточнения выполнить её же)
    proposed_due: datetime | None = None  # предложенный день переноса сохраняется при уточнении только часов
    selection: list[Item] = field(default_factory=list)  # снимок записей, показанных перед массовым удалением
    period: tuple[datetime, datetime] | None = None


@dataclass
class Session:
    collect_details: bool = False
    last_ids: list[int] = field(default_factory=list)
    occ_dates: dict[int, date] = field(default_factory=dict)  # у серий в last_ids — какое повторение было показано
    pending: Pending | None = None
    history: list[tuple[str, str]] = field(default_factory=list)
    touched: float = field(default_factory=_time.monotonic)


class SessionStore:
    """Сессии в памяти процесса. Для нескольких инстансов заменить на Redis/БД с тем же интерфейсом."""

    def __init__(self, ttl_seconds: int = 3 * 3600):
        self.ttl = ttl_seconds
        self._data: dict[tuple[str, str], Session] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str, user_id: str = "demo") -> Session:
        now = _time.monotonic()
        with self._lock:
            expired = [k for k, s in self._data.items() if now - s.touched > self.ttl]
            for k in expired:
                del self._data[k]
            session = self._data.setdefault((user_id, session_id), Session())
            session.touched = now
            return session

    def reset(self, session_id: str, user_id: str = "demo") -> None:
        with self._lock:
            self._data.pop((user_id, session_id), None)
