"""Экспорт записей в iCalendar (.ics, RFC 5545) — для импорта или подписки в Google / Яндекс / Apple Календаре.

- Встречи — события с длительностью; задачи и напоминания — короткие события (или на весь день, если
  время не указано); напоминаниям и встречам добавляется оповещение (VALARM).
- Серии выгружаются одной записью с RRULE — календарь сам развернёт повторения.
- Время пишется в часовом поясе пользователя (TZID). Для поясов без перехода на летнее время
  (как Москва) добавляется точное описание VTIMEZONE; для остальных — «плавающее» местное время.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone, tzinfo

from app.models import DEFAULT_MEETING_MINUTES, Item, ItemStatus, ItemType

PRODID = "-//voice-planner//RU"
_KIND = {ItemType.MEETING: "Встреча", ItemType.TASK: "Задача", ItemType.REMINDER: "Напоминание"}


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Строки длиннее 75 байт переносятся (RFC 5545, 3.1), не разрывая UTF-8 символы."""
    out, current, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > 75:
            out.append(current)
            current, size = " " + ch, 1 + n
        else:
            current += ch
            size += n
    out.append(current)
    return "\r\n".join(out)


def _offset(tz: tzinfo, moment: datetime) -> timedelta:
    return moment.replace(tzinfo=tz).utcoffset() or timedelta()


def _fmt_offset(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}{minutes % 60:02d}"


def _dt(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _d(value: date) -> str:
    return value.strftime("%Y%m%d")


def build_calendar(items: Iterable[Item], tz_name: str, tz: tzinfo, now: datetime,
                   name: str = "Голосовой ежедневник") -> str:
    year = now.year
    fixed = _offset(tz, datetime(year, 1, 15)) == _offset(tz, datetime(year, 7, 15))
    tzid = f";TZID={tz_name}" if fixed else ""

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_escape(name)}", f"X-WR-TIMEZONE:{tz_name}", "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
             "X-PUBLISHED-TTL:PT1H"]
    if fixed:
        off = _fmt_offset(_offset(tz, now))
        lines += ["BEGIN:VTIMEZONE", f"TZID:{tz_name}", "BEGIN:STANDARD", "DTSTART:19700101T000000",
                  f"TZOFFSETFROM:{off}", f"TZOFFSETTO:{off}", "END:STANDARD", "END:VTIMEZONE"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for it in items:
        if it.due_at is None:
            continue
        done = it.status == ItemStatus.DONE and not it.recurrence
        summary = ("✓ " if done else "") + it.title
        lines += ["BEGIN:VEVENT", f"UID:item-{it.id}@voice-planner", f"DTSTAMP:{stamp}"]
        if it.has_time:
            minutes = it.duration_min or (DEFAULT_MEETING_MINUTES if it.type == ItemType.MEETING else 15)
            lines += [f"DTSTART{tzid}:{_dt(it.due_at)}",
                      f"DTEND{tzid}:{_dt(it.due_at + timedelta(minutes=minutes))}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{_d(it.due_at.date())}",
                      f"DTEND;VALUE=DATE:{_d(it.due_at.date() + timedelta(days=1))}"]
        if it.recurrence:
            lines.append(f"RRULE:{it.recurrence}")
            # удалённые и перенесённые отдельно повторения исключаются из серии
            for day in it.skipped_dates:
                if it.has_time:
                    lines.append(f"EXDATE{tzid}:{_dt(datetime.combine(day, it.due_at.time()))}")
                else:
                    lines.append(f"EXDATE;VALUE=DATE:{_d(day)}")
        description = _KIND[it.type] + (' (выполнено)' if done else '')
        if it.notes:
            description += "\n" + it.notes
        lines += [f"SUMMARY:{_escape(summary)}", f"CATEGORIES:{_KIND[it.type]}",
                  f"DESCRIPTION:{_escape(description)}",
                  "TRANSP:OPAQUE" if it.type == ItemType.MEETING else "TRANSP:TRANSPARENT"]
        if it.has_time and not done and it.type in (ItemType.REMINDER, ItemType.MEETING):
            trigger = f"-PT{it.remind_before_min}M" if it.remind_before_min else "PT0M"
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(it.title)}",
                      f"TRIGGER:{trigger}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
