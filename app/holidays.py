"""Государственные праздники России и переносы выходных — для календаря и ответов ассистента.

Праздники — ст. 112 Трудового кодекса РФ. Если праздник (кроме январских) выпадает на субботу или воскресенье,
выходной переносится на следующий рабочий день — это считается автоматически. Январские выходные и «мостики»
переносит правительство отдельным постановлением на каждый год: известные переносы — в DECREES.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

HOLIDAY, DAY_OFF, WORKDAY = "holiday", "day_off", "workday"

_NEW_YEAR = "Новогодние каникулы"
FIXED: dict[tuple[int, int], str] = {
    **{(1, d): _NEW_YEAR for d in (1, 2, 3, 4, 5, 6, 8)},
    (1, 7): "Рождество Христово",
    (2, 23): "День защитника Отечества",
    (3, 8): "Международный женский день",
    (5, 1): "Праздник Весны и Труда",
    (5, 9): "День Победы",
    (6, 12): "День России",
    (11, 4): "День народного единства",
}

# Переносы выходных по постановлениям правительства: (с какого дня, на какой).
# Если «с какого» — январский праздник на выходных, он остаётся праздником, а выходным становится второй день;
# если обычная суббота — она становится рабочей.
DECREES: dict[int, tuple[str, list[tuple[date, date]]]] = {
    2026: ("постановление № 1466 от 24.09.2025", [
        (date(2026, 1, 3), date(2026, 1, 9)),
        (date(2026, 1, 4), date(2026, 12, 31)),
    ]),
    2027: ("постановление № 1187 от 17.09.2026", [
        (date(2027, 1, 2), date(2027, 11, 5)),
        (date(2027, 1, 3), date(2027, 12, 31)),
        (date(2027, 2, 20), date(2027, 2, 22)),
    ]),
}

_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря"]


@dataclass(frozen=True)
class Holiday:
    day: date
    name: str
    kind: str  # holiday — праздник, day_off — перенесённый выходной, workday — рабочая суббота

    @property
    def off(self) -> bool:
        return self.kind != WORKDAY


def _day_label(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]}"


@lru_cache(maxsize=16)
def year_days(year: int) -> dict[date, Holiday]:
    days: dict[date, Holiday] = {date(year, m, d): Holiday(date(year, m, d), name, HOLIDAY)
                                 for (m, d), name in FIXED.items()}
    for src, dst in DECREES.get(year, ("", []))[1]:
        if src not in days:
            kind = "Рабочая суббота" if src.weekday() == 5 else "Рабочий день"
            days[src] = Holiday(src, f"{kind} (выходной перенесён на {_day_label(dst)})", WORKDAY)
        days[dst] = Holiday(dst, f"Выходной (перенос с {_day_label(src)})", DAY_OFF)

    def working(d: date) -> bool:
        h = days.get(d)
        return (h is not None and h.kind == WORKDAY) or (d.weekday() < 5 and h is None)

    for m, d in FIXED:
        day = date(year, m, d)
        if m == 1 or day.weekday() < 5:
            continue  # январские выходные переносит постановление, будние праздники переносить не нужно
        nxt = day + timedelta(days=1)
        while not working(nxt):
            nxt += timedelta(days=1)
        days[nxt] = Holiday(nxt, f"Выходной (перенос с {_day_label(day)})", DAY_OFF)
    return days


def holiday_on(d: date) -> Holiday | None:
    return year_days(d.year).get(d)


def is_day_off(d: date) -> bool:
    h = holiday_on(d)
    return h.off if h is not None else d.weekday() >= 5


def between(start: date, end: date) -> list[Holiday]:
    """Праздники, перенесённые выходные и рабочие субботы в [start, end)."""
    out = []
    for year in range(start.year, end.year + 1):
        out += [h for d, h in year_days(year).items() if start <= d < end]
    return sorted(out, key=lambda h: h.day)


def runs(items: list[Holiday]) -> list[tuple[date, date, str]]:
    """Подряд идущие дни с одним названием — одной строкой: «1–6 января — Новогодние каникулы»."""
    out: list[tuple[date, date, str]] = []
    for h in items:
        if out and out[-1][2] == h.name and (h.day - out[-1][1]).days == 1:
            out[-1] = (out[-1][0], h.day, h.name)
        else:
            out.append((h.day, h.day, h.name))
    return out
