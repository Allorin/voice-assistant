"""Формулировки ответов: даты словами, склонения, списки. Отдельно текст для экрана и для озвучки."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.models import TYPE_FORMS, Item, ItemStatus, ItemType
from app.recurrence import WEEKDAYS_WORK, WEEKEND, Recurrence

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]
MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре",
               "октябре", "ноябре", "декабре"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WD_NOM = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WD_ACC = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
WD_GEN = ["понедельника", "вторника", "среды", "четверга", "пятницы", "субботы", "воскресенья"]

RECORD_FORMS = ("запись", "записи", "записей")
_GENDER = {ItemType.TASK: "f", ItemType.MEETING: "f", ItemType.REMINDER: "n", None: "f"}
_SMALL = {1: {"f": "одна", "n": "одно", "m": "один"}, 2: {"f": "две", "n": "два", "m": "два"}, 3: "три",
          4: "четыре", 5: "пять", 6: "шесть", 7: "семь", 8: "восемь", 9: "девять", 10: "десять"}


def plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def count_phrase(n: int, item_type: ItemType | None, spoken: bool = False) -> str:
    forms = TYPE_FORMS.get(item_type, RECORD_FORMS) if item_type else RECORD_FORMS
    num: str = str(n)
    if spoken and n in _SMALL:
        word = _SMALL[n]
        num = word[_GENDER[item_type]] if isinstance(word, dict) else word
    return f"{num} {plural(n, forms)}"


def type_label(item_type: ItemType) -> str:
    return TYPE_FORMS[item_type][0]


def hm(dt: datetime) -> str:
    return f"{dt.hour}:{dt.minute:02d}"


def day_phrase(d: date, today: date) -> str:
    """«сегодня», «завтра», «в пятницу, 23 октября», «14 сентября 2025»."""
    delta = (d - today).days
    names = {0: "сегодня", 1: "завтра", 2: "послезавтра", -1: "вчера", -2: "позавчера"}
    if delta in names:
        return names[delta]
    base = f"{d.day} {MONTHS_GEN[d.month - 1]}" + (f" {d.year}" if d.year != today.year else "")
    if -7 < delta < 7:
        return f"{WD_ACC[d.weekday()]}, {base}"
    return base


def when_phrase(due: datetime | None, has_time: bool, today: date) -> str:
    if due is None:
        return "без срока"
    day = day_phrase(due.date(), today)
    return f"{day} в {hm(due)}" if has_time else day


WD_ACC_BARE = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]


def on_day_phrase(d: date, today: date) -> str:
    """«на завтра», «на пятницу, 23 октября», «на 5 ноября»."""
    delta = (d - today).days
    names = {0: "на сегодня", 1: "на завтра", 2: "на послезавтра"}
    if delta in names:
        return names[delta]
    base = f"{d.day} {MONTHS_GEN[d.month - 1]}" + (f" {d.year}" if d.year != today.year else "")
    if 0 < delta < 7:
        return f"на {WD_ACC_BARE[d.weekday()]}, {base}"
    return f"на {base}"


def deadline_phrase(due: datetime, today: date) -> str:
    delta = (due.date() - today).days
    if delta == 0:
        return "до конца дня"
    if delta == 1:
        return "до завтра"
    if 1 < delta < 7:
        return f"до {WD_GEN[due.weekday()]}, {due.day} {MONTHS_GEN[due.month - 1]}"
    return f"до {due.day} {MONTHS_GEN[due.month - 1]}" + (f" {due.year}" if due.year != today.year else "")


def short_when(item: Item, today: date) -> str:
    """Компактно для списков: «сегодня 18:00», «пт 23 окт», «14 сен 2025, 15:15»."""
    if item.due_at is None:
        return "без срока"
    d = item.due_at.date()
    delta = (d - today).days
    if delta in (0, 1, -1):
        day = {0: "сегодня", 1: "завтра", -1: "вчера"}[delta]
    else:
        day = f"{d.day} {MONTHS_SHORT[d.month - 1]}" + (f" {d.year}" if d.year != today.year else "")
    return f"{day}, {hm(item.due_at)}" if item.has_time else day


WD_EVERY = ["каждый понедельник", "каждый вторник", "каждую среду", "каждый четверг", "каждую пятницу",
            "каждую субботу", "каждое воскресенье"]
WD_DAT_PL = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]


def _join_and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " и " + words[-1]


def recurrence_text(rule: Recurrence | None, start: datetime | None = None) -> str:
    """«каждый день», «по будням», «каждый понедельник», «по вторникам и четвергам», «каждые 2 недели по пятницам»."""
    if rule is None:
        return ""
    n = rule.interval
    days = rule.byday or ((start.weekday(),) if (rule.freq == "WEEKLY" and start) else ())
    if rule.freq == "DAILY":
        return "каждый день" if n == 1 else f"каждые {n} {plural(n, ('день', 'дня', 'дней'))}"
    if rule.freq == "WEEKLY":
        if tuple(days) == WEEKDAYS_WORK and n == 1:
            return "по будням"
        if tuple(days) == WEEKEND and n == 1:
            return "по выходным"
        if n == 1:
            if len(days) == 1:
                return WD_EVERY[days[0]]
            return "по " + _join_and([WD_DAT_PL[d] for d in days]) if days else "каждую неделю"
        head = f"каждые {n} {plural(n, ('неделю', 'недели', 'недель'))}"
        return head + (" по " + _join_and([WD_DAT_PL[d] for d in days]) if days else "")
    if rule.freq == "MONTHLY":
        day = rule.bymonthday or (start.day if start else None)
        head = "каждый месяц" if n == 1 else f"каждые {n} {plural(n, ('месяц', 'месяца', 'месяцев'))}"
        return f"{head} {day}-го числа" if day else head
    if start:
        return f"каждый год {start.day} {MONTHS_GEN[start.month - 1]}"
    return "каждый год"


def series_when(item: Item) -> str:
    """Расписание серии: «каждый понедельник в 10:00»."""
    text = recurrence_text(item.rule, item.due_at)
    if item.has_time and item.due_at:
        text += f" в {hm(item.due_at)}"
    return text


def item_line(item: Item, today: date, with_date: bool = True, with_type: bool = True) -> str:
    mark = "✓ " if item.status == ItemStatus.DONE else ""
    when = short_when(item, today) if with_date else (hm(item.due_at) if item.has_time and item.due_at else "весь день")
    kind = f" ({type_label(item.type)})" if with_type else ""
    repeat = f", {recurrence_text(item.rule, item.due_at)}" if item.recurrence else ""
    return f"{when} — {mark}{item.title}{kind}{repeat}"


def spoken_item(item: Item, today: date, with_date: bool) -> str:
    t = item.title
    title = t if len(t) > 1 and t[1].isupper() else t[:1].lower() + t[1:]  # «CRM ...» не трогаем
    if with_date:
        return f"{when_phrase(item.due_at, item.has_time, today)} — {title}"
    if item.has_time and item.due_at:
        return f"в {hm(item.due_at)} — {title}"
    return title


def period_label(start: datetime, end: datetime, today: date) -> str:
    """Подпись периода для начала фразы: «Сегодня», «На этой неделе», «В октябре», «С 20 по 25 октября»."""
    s, e = start.date(), (end - timedelta(seconds=1)).date()
    if s == e:
        return cap(day_phrase(s, today))
    monday = today - timedelta(days=today.weekday())
    if start.time() == datetime.min.time() and (end - start).days == 7 and s.weekday() == 0:
        if s == monday:
            return "На этой неделе"
        if s == monday + timedelta(days=7):
            return "На следующей неделе"
        if s == monday - timedelta(days=7):
            return "На прошлой неделе"
    if s.weekday() == 5 and (e - s).days == 1:
        return "В выходные"
    if s.day == 1 and (e + timedelta(days=1)).day == 1 and s.month == e.month:
        return f"В {MONTHS_PREP[s.month - 1]}" + (f" {s.year}" if s.year != today.year else "")
    if s.month == e.month:
        return f"С {s.day} по {e.day} {MONTHS_GEN[e.month - 1]}"
    return f"С {s.day} {MONTHS_GEN[s.month - 1]} по {e.day} {MONTHS_GEN[e.month - 1]}"


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def group_by_day(items: list[Item]) -> list[tuple[date, list[Item]]]:
    groups: dict[date, list[Item]] = {}
    for it in items:
        if it.due_at is not None:
            groups.setdefault(it.due_at.date(), []).append(it)
    return sorted(groups.items())


def day_header(d: date, today: date) -> str:
    delta = (d - today).days
    prefix = {0: "Сегодня", 1: "Завтра", -1: "Вчера"}.get(delta)
    base = f"{WD_NOM[d.weekday()]}, {d.day} {MONTHS_GEN[d.month - 1]}" + (
        f" {d.year}" if d.year != today.year else "")
    return f"{prefix}, {base}" if prefix else cap(base)


HELP_TEXT = (
    "Я веду задачи, напоминания и встречи. Можно говорить своими словами, например:\n"
    "• «Напомни завтра в 10 утра позвонить Ивану»\n"
    "• «В четверг в 11 у меня встреча с дизайнером»\n"
    "• «Что у меня сегодня?» или «Что запланировано на эту неделю?»\n"
    "• «Найди задачу про презентацию», «Когда была встреча с Иваном?»\n"
    "• «Перенеси её на пятницу», «Отметь выполненной», «Сними отметку», «Удали встречу с дизайнером»\n"
    "• «Каждый понедельник в 10 у меня планёрка», «Напоминай по будням в 9 пить воду»"
)
HELP_SPEECH = ("Я веду задачи, напоминания и встречи. Скажите, например: напомни завтра в десять позвонить Ивану, "
               "или: что у меня сегодня.")
