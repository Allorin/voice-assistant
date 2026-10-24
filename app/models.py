"""Доменные модели: запись (задача / встреча / напоминание) и её поля."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field

from app.recurrence import Recurrence


class ItemType(str, Enum):
    TASK = "task"
    MEETING = "meeting"
    REMINDER = "reminder"


class ItemStatus(str, Enum):
    OPEN = "open"
    DONE = "done"


# Склонения для ответов: (1 задача, 2 задачи, 5 задач)
TYPE_FORMS: dict[ItemType, tuple[str, str, str]] = {
    ItemType.TASK: ("задача", "задачи", "задач"),
    ItemType.MEETING: ("встреча", "встречи", "встреч"),
    ItemType.REMINDER: ("напоминание", "напоминания", "напоминаний"),
}

DEFAULT_MEETING_MINUTES = 60


class Item(BaseModel):
    id: int
    user_id: str
    type: ItemType
    title: str
    due_at: datetime | None = None      # срок задачи / начало встречи / момент напоминания; у серии — первое повторение
    has_time: bool = False              # False — указан только день
    duration_min: int | None = None     # длительность встречи (для поиска конфликтов)
    status: ItemStatus = ItemStatus.OPEN
    created_at: datetime
    updated_at: datetime
    source: str = "user"                # user | import
    recurrence: str | None = None       # правило повторения (RRULE), None — разовая запись
    done_dates: list[date] = Field(default_factory=list)  # выполненные повторения серии
    skipped_dates: list[date] = Field(default_factory=list)  # даты, исключённые из серии (удалены или перенесены)
    series_id: int | None = None        # у перенесённого повторения — id серии
    original_date: date | None = None   # … и дата, вместо которой оно стоит
    occurrence: bool = False            # True — это одно повторение серии (не хранится в БД)

    @property
    def end_at(self) -> datetime | None:
        if self.due_at is None or not self.has_time:
            return None
        return self.due_at + timedelta(minutes=self.duration_min or DEFAULT_MEETING_MINUTES)

    @property
    def rule(self) -> Recurrence | None:
        return Recurrence.parse(self.recurrence)

    def occurrence_at(self, moment: datetime) -> Item:
        """Одно повторение серии со своим статусом выполнения."""
        done = moment.date() in self.done_dates
        return self.model_copy(update={
            "due_at": moment, "occurrence": True,
            "status": ItemStatus.DONE if done else ItemStatus.OPEN,
        })


class NewItem(BaseModel):
    type: ItemType
    title: str
    due_at: datetime | None = None
    has_time: bool = False
    duration_min: int | None = None
    status: ItemStatus = ItemStatus.OPEN
    source: str = "user"
    recurrence: str | None = None
    series_id: int | None = None
    original_date: date | None = None
