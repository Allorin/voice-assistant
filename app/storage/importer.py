"""Импорт исходного CSV (id, date, time, text).

В CSV нет типа записи, поэтому он определяется по первому слову текста:
«Встреча / Созвон / Демо / Провести / Обсудить» — встреча, «Напомнить» — напоминание, остальное — задача.
Падежи в названиях исправляются («Позвонить Сергеем» → «Позвонить Сергею»), см. app/grammar.py.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from app.grammar import fix_cases
from app.models import DEFAULT_MEETING_MINUTES, ItemType, NewItem
from app.storage.repository import ItemRepository

_MEETING_HEADS = ("встреча", "созвон", "демо", "провести", "обсудить", "совещание", "планёрка", "планерка",
                  "синк", "ретро")
_REMINDER_HEADS = ("напомнить",)


def classify_title(title: str) -> ItemType:
    head = title.strip().split(maxsplit=1)[0].lower() if title.strip() else ""
    if head in _MEETING_HEADS:
        return ItemType.MEETING
    if head in _REMINDER_HEADS:
        return ItemType.REMINDER
    return ItemType.TASK


def read_csv(path: str | Path) -> list[NewItem]:
    items: list[NewItem] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            text = fix_cases((row.get("text") or "").strip())
            if not text:
                continue
            date_s = (row.get("date") or "").strip()
            time_s = (row.get("time") or "").strip()
            due = None
            has_time = False
            if date_s:
                if time_s:
                    due = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
                    has_time = True
                else:
                    due = datetime.strptime(date_s, "%Y-%m-%d")
            item_type = classify_title(text)
            items.append(
                NewItem(
                    type=item_type,
                    title=text,
                    due_at=due,
                    has_time=has_time,
                    duration_min=DEFAULT_MEETING_MINUTES if item_type == ItemType.MEETING else None,
                    source="import",
                )
            )
    return items


def import_csv(repo: ItemRepository, path: str | Path, user_id: str, now: datetime) -> int:
    return repo.add_many(user_id, read_csv(path), now)
