"""Репозиторий записей: единственное место, которое знает про SQL."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import Item, ItemStatus, ItemType, NewItem
from app.storage.db import ItemRow, UserRow


def _parse_dates(value: str | None) -> list[date]:
    out = []
    for part in (value or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(date.fromisoformat(part))
            except ValueError:
                continue
    return sorted(set(out))


def _dump_dates(values: Iterable[date]) -> str:
    return ",".join(d.isoformat() for d in sorted(set(values)))


def _to_item(row: ItemRow) -> Item:
    return Item(
        id=row.id,
        user_id=row.user_id,
        type=ItemType(row.type),
        title=row.title,
        notes=row.notes or "",
        remind_before_min=row.remind_before_min or 0,
        due_at=row.due_at,
        has_time=row.has_time,
        duration_min=row.duration_min,
        status=ItemStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        source=row.source,
        recurrence=row.recurrence or None,
        done_dates=_parse_dates(row.done_dates),
        skipped_dates=_parse_dates(row.skipped_dates),
        series_id=row.series_id,
        original_date=date.fromisoformat(row.original_date) if row.original_date else None,
    )


def _new_row(user_id: str, d: NewItem, now: datetime) -> ItemRow:
    return ItemRow(
        user_id=user_id,
        type=d.type.value,
        title=d.title,
        notes=d.notes,
        remind_before_min=d.remind_before_min,
        due_at=d.due_at,
        has_time=d.has_time,
        duration_min=d.duration_min,
        status=d.status.value,
        created_at=now,
        updated_at=now,
        source=d.source,
        recurrence=d.recurrence,
        done_dates="",
        skipped_dates="",
        series_id=d.series_id,
        original_date=d.original_date.isoformat() if d.original_date else None,
    )


class ItemRepository:
    def __init__(self, session_factory: sessionmaker):
        self._sf = session_factory

    def add(self, user_id: str, data: NewItem, now: datetime) -> Item:
        with self._sf() as s:
            row = _new_row(user_id, data, now)
            s.add(row)
            s.commit()
            return _to_item(row)

    def add_many(self, user_id: str, items: Iterable[NewItem], now: datetime) -> int:
        rows = [_new_row(user_id, d, now) for d in items]
        with self._sf() as s:
            s.add_all(rows)
            s.commit()
        return len(rows)

    def get(self, user_id: str, item_id: int) -> Item | None:
        with self._sf() as s:
            row = s.get(ItemRow, item_id)
            if row is None or row.user_id != user_id:
                return None
            return _to_item(row)

    def update(self, user_id: str, item_id: int, now: datetime, **fields) -> Item | None:
        with self._sf() as s:
            row = s.get(ItemRow, item_id)
            if row is None or row.user_id != user_id:
                return None
            for key, value in fields.items():
                if key in ("done_dates", "skipped_dates"):
                    value = _dump_dates(value)
                elif hasattr(value, "value"):  # Enum -> str
                    value = value.value
                setattr(row, key, value)
            row.updated_at = now
            s.commit()
            return _to_item(row)

    def delete(self, user_id: str, item_id: int) -> bool:
        with self._sf() as s:
            res = s.execute(delete(ItemRow).where(ItemRow.id == item_id, ItemRow.user_id == user_id))
            s.commit()
            return res.rowcount > 0

    def delete_series_children(self, user_id: str, series_id: int) -> int:
        """Удалить перенесённые повторения серии (вместе с самой серией)."""
        with self._sf() as s:
            res = s.execute(delete(ItemRow).where(ItemRow.series_id == series_id, ItemRow.user_id == user_id))
            s.commit()
            return res.rowcount

    def delete_confirmed(self, user_id: str, selection: list[Item], now: datetime) -> bool:
        """Удалить проверенный снимок одной транзакцией; у серий исключить только выбранные даты.

        Если запись изменилась после показа подтверждения, ничего не удаляется.
        """
        if not selection:
            return True
        ids = {item.id for item in selection}
        with self._sf.begin() as s:
            if s.bind.dialect.name == "sqlite":
                s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            stmt = select(ItemRow).where(ItemRow.user_id == user_id, ItemRow.id.in_(ids)).with_for_update()
            rows = {row.id: row for row in s.scalars(stmt)}
            skipped: dict[int, set[date]] = {}
            for expected in selection:
                if expected.recurrence and not expected.occurrence:
                    return False
                row = rows.get(expected.id)
                if row is None or expected.user_id != user_id:
                    return False
                current = _to_item(row)
                if expected.occurrence:
                    if current.rule is None or expected.due_at is None:
                        return False
                    current = current.occurrence_at(expected.due_at)
                    skipped.setdefault(row.id, set()).add(expected.due_at.date())
                if current != expected:
                    return False
            for item_id, row in rows.items():
                if item_id in skipped:
                    row.skipped_dates = _dump_dates([*_parse_dates(row.skipped_dates), *skipped[item_id]])
                    row.updated_at = now
                else:
                    s.delete(row)
        return True

    def list_between(
        self,
        user_id: str,
        start: datetime | None,
        end: datetime | None,
        types: Iterable[ItemType] | None = None,
        statuses: Iterable[ItemStatus] | None = None,
        recurring: bool | None = None,
    ) -> list[Item]:
        """Записи с датой в [start, end). recurring: None — все, False — только разовые, True — только серии."""
        stmt = select(ItemRow).where(ItemRow.user_id == user_id, ItemRow.due_at.is_not(None))
        if start is not None:
            stmt = stmt.where(ItemRow.due_at >= start)
        if end is not None:
            stmt = stmt.where(ItemRow.due_at < end)
        if types:
            stmt = stmt.where(ItemRow.type.in_([t.value for t in types]))
        if statuses:
            stmt = stmt.where(ItemRow.status.in_([s.value for s in statuses]))
        if recurring is True:
            stmt = stmt.where(ItemRow.recurrence.is_not(None), ItemRow.recurrence != "")
        elif recurring is False:
            stmt = stmt.where(or_(ItemRow.recurrence.is_(None), ItemRow.recurrence == ""))
        stmt = stmt.order_by(ItemRow.due_at, ItemRow.id)
        with self._sf() as s:
            return [_to_item(r) for r in s.scalars(stmt)]

    def list_recurring(self, user_id: str, types: Iterable[ItemType] | None = None,
                       before: datetime | None = None) -> list[Item]:
        """Серии, которые начались раньше before (их повторения разворачивает сервис)."""
        return self.list_between(user_id, None, before, types, None, recurring=True)

    def list_undated(self, user_id: str, statuses: Iterable[ItemStatus] | None = None) -> list[Item]:
        stmt = select(ItemRow).where(ItemRow.user_id == user_id, ItemRow.due_at.is_(None))
        if statuses:
            stmt = stmt.where(ItemRow.status.in_([s.value for s in statuses]))
        with self._sf() as s:
            return [_to_item(r) for r in s.scalars(stmt.order_by(ItemRow.created_at))]

    def list_all(self, user_id: str) -> list[Item]:
        with self._sf() as s:
            stmt = select(ItemRow).where(ItemRow.user_id == user_id).order_by(ItemRow.id)
            return [_to_item(r) for r in s.scalars(stmt)]

    def last_completed(self, user_id: str) -> Item | None:
        """Последняя отмеченная запись — для «сними отметку» без уточнения, какую."""
        stmt = (select(ItemRow)
                .where(ItemRow.user_id == user_id,
                       or_(ItemRow.status == ItemStatus.DONE.value, ItemRow.done_dates != ""))
                .order_by(ItemRow.updated_at.desc(), ItemRow.id.desc())
                .limit(1))
        with self._sf() as s:
            row = s.scalars(stmt).first()
            return _to_item(row) if row else None

    def ensure_user(self, user_id: str, now: datetime) -> bool:
        """Запомнить профиль. True — профиль встретился впервые."""
        with self._sf() as s:
            if s.get(UserRow, user_id) is not None:
                return False
            s.add(UserRow(id=user_id, created_at=now))
            try:
                s.commit()
            except IntegrityError:  # тот же профиль одновременно создал другой запрос
                s.rollback()
                return False
            return True

    def count(self, user_id: str | None = None) -> int:
        with self._sf() as s:
            stmt = select(func.count(ItemRow.id))
            if user_id is not None:
                stmt = stmt.where(ItemRow.user_id == user_id)
            return int(s.scalar(stmt) or 0)
