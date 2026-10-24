"""Структура команды, в которую разборщик (LLM или правила) превращает фразу пользователя.

Важно: в команде время хранится фразой («завтра в 10 утра»), а не датой. Точную дату считает
app/timeparse — так результат не зависит от того, насколько хорошо модель умеет в календарь.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol

from pydantic import BaseModel

from app.models import ItemType


class Intent(str, Enum):
    CREATE = "create"        # создать задачу / встречу / напоминание
    LIST = "list"            # что запланировано за период
    SEARCH = "search"        # найти запись по смыслу
    UPDATE = "update"        # перенести / переименовать
    DELETE = "delete"
    COMPLETE = "complete"    # отметить выполненной
    REOPEN = "reopen"        # снять отметку о выполнении
    CANCEL = "cancel"        # «отмена», «не надо»
    HELP = "help"
    SMALLTALK = "smalltalk"  # привет / спасибо
    UNKNOWN = "unknown"


class Command(BaseModel):
    intent: Intent
    item_type: ItemType | None = None   # create: тип записи; list/search: фильтр по типу
    title: str | None = None            # create: содержание; update: новое название
    when: str | None = None             # create/update: фраза о времени
    repeat: str | None = None           # create: фраза о повторении («каждый понедельник»)
    period: str | None = None           # list/search/bulk delete: фраза периода
    bulk: bool = False                  # delete: все подходящие записи за период, с подтверждением
    query: str | None = None            # search: что ищем; update/delete/complete/reopen: какую запись
    occurrence: str | None = None       # update/delete/...: какое повторение серии («в понедельник»), фраза
    scope: Literal["one", "series"] | None = None  # только это повторение / вся серия
    target_last: bool = False           # «её», «эту» — последняя обсуждавшаяся запись
    tense: Literal["past", "future", "any"] = "any"
    status: Literal["open", "done"] | None = None  # list/search: только невыполненные / выполненные
    ask_when: bool = False              # вопрос «когда ...?» — ответить одной датой
    reply: str | None = None            # готовый ответ для smalltalk (от LLM)
    source: str = "rules"               # кто разобрал: rules | llm


@dataclass
class NLUContext:
    now: datetime
    last_item_title: str | None = None
    history: list[tuple[str, str]] = field(default_factory=list)  # (пользователь, ассистент)


class NLU(Protocol):
    def parse(self, text: str, ctx: NLUContext) -> Command: ...
