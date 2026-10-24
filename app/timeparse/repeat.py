"""Разбор повторений: «каждый понедельник», «по будням», «каждые 2 недели», «15 числа каждого месяца».

Как и с датами, правило вычисляется детерминированно: LLM (если подключена) только копирует фразу.
Найденная фраза вырезается из текста, остаток передаётся обычному парсеру дат — он найдёт время («в 10»)
и, если назван, день начала («с 1 ноября»).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time

from app.recurrence import WEEKDAYS_WORK, WEEKEND, Recurrence
from app.timeparse.ru import _Text, join_tokens, tokenize

_B = r"(?<![а-яa-z0-9#])"
_E = r"(?![а-яa-z0-9])"
_EACH = r"(?:каждый|каждое|каждую|каждые|каждого|каждой|каждом)"
_WD = (r"(?:понедельник(?:ам|а)?|вторник(?:ам|а)?|сред(?:у|ам|а)|четверг(?:ам|а)?|пятниц(?:у|ам|а)"
       r"|суббот(?:у|ам|а)|воскресень(?:е|ям|я))")
# «каждую пятницу» — винительный падеж; «по пятницам» — только множественное число («по пятницу» = «до пятницы»)
_WD_ONE = r"(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)"
_WD_MANY = r"(?:понедельникам|вторникам|средам|четвергам|пятницам|субботам|воскресеньям)"
_WD_INDEX = {"пон": 0, "вто": 1, "сре": 2, "чет": 3, "пят": 4, "суб": 5, "вос": 6}
_TOD_TIME = {"утр": time(9, 0), "веч": time(19, 0), "обед": time(13, 0), "ноч": time(23, 0)}


@dataclass
class RepeatInfo:
    rule: Recurrence | None = None
    phrase: str = ""              # вырезанная фраза о повторении
    rest: str = ""                # текст без неё
    default_time: time | None = None  # «каждое утро» без точного времени → 9:00


@dataclass
class _Parts:
    freq: str | None = None
    interval: int = 1
    byday: tuple[int, ...] = ()
    bymonthday: int | None = None
    tod: time | None = None


def _weekdays(fragment: str) -> tuple[int, ...]:
    found = re.findall(_WD, fragment)
    return tuple(sorted({_WD_INDEX[w[:3]] for w in found}))


def _unit_freq(unit: str) -> str:
    if unit.startswith(("дн", "ден", "сут")):
        return "DAILY"
    if unit.startswith("недел"):
        return "WEEKLY"
    if unit.startswith("месяц"):
        return "MONTHLY"
    return "YEARLY"


_UNIT = r"(?P<unit>день|дня|дней|сутки|суток|неделю|недели|недель|месяц|месяца|месяцев|год|года|лет)"


def parse_repeat(text: str) -> RepeatInfo:
    toks = tokenize(text)
    t = _Text(toks)
    p = _Parts()

    def rule(pattern: str, handler) -> None:
        rx = re.compile(_B + pattern + _E)
        pos = 0
        while True:
            m = rx.search(t.s, pos)
            if not m:
                return
            if handler(m) is False:
                pos = m.start() + 1
                continue
            t.consume(m.start(), m.end())
            pos = m.end()

    def workdays(_):
        p.freq, p.byday = "WEEKLY", WEEKDAYS_WORK

    def weekend(_):
        p.freq, p.byday = "WEEKLY", WEEKEND

    def weekdays(m):
        days = _weekdays(m.group(0))
        if not days:
            return False
        p.freq = "WEEKLY"
        p.byday = tuple(sorted(set(p.byday) | set(days)))

    def every_n(m):
        n = int(m.group("n") or 1)
        if n < 1 or n > 365:
            return False
        p.freq = p.freq if (p.freq == "WEEKLY" and p.byday and _unit_freq(m.group("unit")) == "WEEKLY") \
            else _unit_freq(m.group("unit"))
        p.interval = n

    def daily_tod(m):
        p.freq = p.freq or "DAILY"
        word = m.group("tod")
        for key, value in _TOD_TIME.items():
            if word.startswith(key):
                p.tod = value

    def simple(freq: str):
        def handler(_):
            p.freq = p.freq or freq
        return handler

    def monthday(m):
        d = int(m.group("d"))
        if not 1 <= d <= 31:
            return False
        p.freq, p.bymonthday = "MONTHLY", d

    # порядок важен: сначала длинные конструкции
    rule(r"(?:" + _EACH + r"\s(?:будн\w*|рабоч\w*)\s(?:день|дни)|по\s(?:будням|рабочим\sдням|будним\sдням)"
         r"|в\sбудни|по\sбудним)", workdays)
    rule(r"(?:" + _EACH + r"\sвыходн\w*(?:\sдень|\sдни)?|по\sвыходным|на\sкаждых\sвыходных)", weekend)
    rule(r"(?P<d>\d+)(?:-?е|-?го)?\s(?:числа\s)?" + _EACH + r"\sмесяца", monthday)
    rule(_EACH + r"\s(?P<d>\d+)(?:-?е|-?го)?\s(?:число|числа)", monthday)
    rule(r"(?:каждый\sмесяц|ежемесячно)\s(?P<d>\d{1,2})(?:\sчисла|\sчисло)?(?!\s?[:.]\d)", monthday)
    rule(r"(?:" + _EACH + r"|раз\sв)\s(?:(?P<n>\d+)\s)?" + _UNIT, every_n)
    rule(r"(?:" + _EACH + r"|еженедельно\sв)\s" + _WD_ONE
         + r"(?:\s?(?:,|и)\s(?:" + _EACH + r"\s)?" + _WD_ONE + r")*", weekdays)
    rule(r"(?:по|еженедельно\sпо)\s" + _WD_MANY + r"(?:\s?(?:,|и)\s(?:по\s)?" + _WD_MANY + r")*", weekdays)
    rule(_EACH + r"\s(?P<tod>утро|вечер|обед|ночь)", daily_tod)
    rule(r"по\s(?P<tod>утрам|вечерам)", daily_tod)
    rule(r"(?:ежедневно|ежедневное|ежедневный)", simple("DAILY"))
    rule(r"(?:еженедельно|еженедельная|еженедельный|еженедельную)", simple("WEEKLY"))
    rule(r"(?:ежемесячно|ежемесячная|ежемесячный|ежемесячную)", simple("MONTHLY"))
    rule(r"(?:ежегодно|ежегодная|ежегодный|ежегодную)", simple("YEARLY"))
    rule(r"по\s(?:всем\s)?" + _WD_MANY, weekdays)

    if p.freq is None:
        return RepeatInfo(rest=text)
    if p.byday and p.freq not in ("WEEKLY",):
        p.freq = "WEEKLY"
    rec = Recurrence(p.freq, p.interval, p.byday, p.bymonthday)
    consumed = t.consumed
    rest = join_tokens([tok for i, tok in enumerate(toks) if i not in consumed])
    phrase = " ".join(toks[i].text for i in sorted(consumed))
    return RepeatInfo(rule=rec, phrase=phrase, rest=rest, default_time=p.tod)
