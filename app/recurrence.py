"""Правило повторения записи.

Хранится строкой в формате RRULE из iCalendar (RFC 5545): «FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE».
Тот же формат без преобразований уходит в экспорт .ics, а разворачивание в конкретные даты
делает dateutil.rrule.

Дата начала серии (DTSTART) — это due_at записи: первое повторение.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from dateutil import rrule as du

FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
DAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_DU_FREQ = {"DAILY": du.DAILY, "WEEKLY": du.WEEKLY, "MONTHLY": du.MONTHLY, "YEARLY": du.YEARLY}
WEEKDAYS_WORK = (0, 1, 2, 3, 4)
WEEKEND = (5, 6)
MAX_OCCURRENCES = 2000  # защита от бесконечных выборок


@dataclass(frozen=True)
class Recurrence:
    freq: str                                   # DAILY | WEEKLY | MONTHLY | YEARLY
    interval: int = 1
    byday: tuple[int, ...] = field(default=())  # 0 = понедельник
    bymonthday: int | None = None

    # ---------------------------------------------------------------- формат RRULE

    def to_rrule(self) -> str:
        parts = [f"FREQ={self.freq}", f"INTERVAL={self.interval}"]
        if self.byday:
            parts.append("BYDAY=" + ",".join(DAY_CODES[d] for d in sorted(self.byday)))
        if self.bymonthday:
            parts.append(f"BYMONTHDAY={self.bymonthday}")
        return ";".join(parts)

    @classmethod
    def parse(cls, value: str | None) -> Recurrence | None:
        if not value:
            return None
        data = dict(p.split("=", 1) for p in value.upper().split(";") if "=" in p)
        freq = data.get("FREQ")
        if freq not in FREQS:
            return None
        byday = tuple(sorted(DAY_CODES.index(d) for d in data.get("BYDAY", "").split(",") if d in DAY_CODES))
        bymonthday = int(data["BYMONTHDAY"]) if data.get("BYMONTHDAY", "").lstrip("-").isdigit() else None
        return cls(freq, max(1, int(data.get("INTERVAL", "1") or 1)), byday, bymonthday)

    # ---------------------------------------------------------------- разворачивание

    def _rule(self, dtstart: datetime) -> du.rrule:
        return du.rrule(
            _DU_FREQ[self.freq],
            dtstart=dtstart,
            interval=self.interval,
            byweekday=self.byday or None,
            bymonthday=self.bymonthday,
            cache=False,
        )

    def between(self, dtstart: datetime, start: datetime, end: datetime) -> list[datetime]:
        """Повторения в полуинтервале [start, end)."""
        if end <= start:
            return []
        out = []
        for occ in self._rule(dtstart).xafter(start, inc=True):
            if occ >= end or len(out) >= MAX_OCCURRENCES:
                break
            out.append(occ)
        return out

    def first_on_or_after(self, dtstart: datetime, moment: datetime) -> datetime | None:
        return self._rule(dtstart).after(moment, inc=True)

    def after(self, dtstart: datetime, moment: datetime) -> datetime | None:
        return self._rule(dtstart).after(moment, inc=False)

    def last_before(self, dtstart: datetime, moment: datetime) -> datetime | None:
        return self._rule(dtstart).before(moment, inc=False)

    def iter_from(self, dtstart: datetime, moment: datetime):
        for i, occ in enumerate(self._rule(dtstart).xafter(moment, inc=True)):
            if i >= MAX_OCCURRENCES:
                return
            yield occ

    # ---------------------------------------------------------------- изменения

    def moved_to(self, new_start: datetime, old_start: datetime | None) -> Recurrence:
        """Перенос серии: «каждый понедельник» → «на среду» становится «каждой средой»."""
        if self.freq == "WEEKLY" and len(self.byday) <= 1 and (
                old_start is None or new_start.weekday() != old_start.weekday()):
            return replace(self, byday=(new_start.weekday(),))
        if self.freq == "MONTHLY" and self.bymonthday and (old_start is None or new_start.day != old_start.day):
            return replace(self, bymonthday=new_start.day)
        return self
