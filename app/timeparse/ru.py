"""Детерминированный разбор русских выражений даты и времени.

Зачем отдельный парсер, если есть LLM: бесплатные модели часто ошибаются в арифметике дат
(«в пятницу» → не та пятница, «через два часа» → неверный час). Поэтому LLM только вырезает
фразу о времени («завтра в 10 утра»), а конкретную дату считает этот модуль — его легко покрыть тестами.

Как устроено:
1. Текст режется на токены, слова-числительные заменяются цифрами («два» → 2, «двадцать пятого» → 25).
2. Нормализованная строка прогоняется через набор правил (регулярок) в порядке приоритета.
   Каждое найденное выражение «съедает» свои токены, чтобы правила не пересекались.
3. Найденные части (день, время, сдвиг, период, срок) собираются в итоговый TimeInfo.
Оставшиеся (не съеденные) токены — это содержание задачи, их использует разборщик команд.
"""
from __future__ import annotations

import calendar
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

Prefer = str  # "future" | "past" | "nearest"

# --------------------------------------------------------------------------- токены

_TOKEN_RE = re.compile(
    r"\d{1,2}:\d{2}"
    r"|\d+(?:[./\-]\d+)*(?:-?(?:го|ое|е))?"
    r"|[a-zа-яё]+(?:-[a-zа-яё]+)*"
    r"|[^\s\w]",
    re.IGNORECASE,
)

_UNITS = {
    "один": 1, "одна": 1, "одну": 1, "одного": 1, "одной": 1,
    "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3, "четыре": 4, "четырех": 4,
    "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
    "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
}
_TEENS = {
    "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12, "двенадцати": 12,
    "тринадцать": 13, "тринадцати": 13, "четырнадцать": 14, "четырнадцати": 14,
    "пятнадцать": 15, "пятнадцати": 15, "шестнадцать": 16, "шестнадцати": 16,
    "семнадцать": 17, "семнадцати": 17, "восемнадцать": 18, "восемнадцати": 18,
    "девятнадцать": 19, "девятнадцати": 19,
}
_TENS = {"двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30, "сорок": 40, "сорока": 40,
         "пятьдесят": 50, "пятидесяти": 50}
_ORDINALS = {
    "первое": 1, "первого": 1, "второе": 2, "второго": 2, "третье": 3, "третьего": 3,
    "четвертое": 4, "четвертого": 4, "пятое": 5, "пятого": 5, "шестое": 6, "шестого": 6,
    "седьмое": 7, "седьмого": 7, "восьмое": 8, "восьмого": 8, "девятое": 9, "девятого": 9,
    "десятое": 10, "десятого": 10, "одиннадцатое": 11, "одиннадцатого": 11,
    "двенадцатое": 12, "двенадцатого": 12, "тринадцатое": 13, "тринадцатого": 13,
    "четырнадцатое": 14, "четырнадцатого": 14, "пятнадцатое": 15, "пятнадцатого": 15,
    "шестнадцатое": 16, "шестнадцатого": 16, "семнадцатое": 17, "семнадцатого": 17,
    "восемнадцатое": 18, "восемнадцатого": 18, "девятнадцатое": 19, "девятнадцатого": 19,
    "двадцатое": 20, "двадцатого": 20, "тридцатое": 30, "тридцатого": 30,
}
_SPECIAL = {"пару": "2", "пара": "2", "пары": "2", "полтора": "1.5", "полторы": "1.5",
            "полчаса": "30 минут", "полчасика": "30 минут"}


@dataclass
class Tok:
    text: str   # как в исходной фразе
    norm: str   # нормализованная форма ("" — слово склеено с предыдущим числительным)
    start: int
    end: int


def _num_value(word: str) -> int | None:
    for table in (_UNITS, _TEENS, _TENS, _ORDINALS):
        if word in table:
            return table[word]
    return None


def tokenize(text: str) -> list[Tok]:
    toks: list[Tok] = []
    for m in _TOKEN_RE.finditer(text):
        raw = m.group(0)
        w = raw.lower().replace("ё", "е")
        if w[0].isdigit():
            norm = re.sub(r"-?(?:го|ое|е)$", "", w)
        elif w in _SPECIAL:
            norm = _SPECIAL[w]
        else:
            v = _num_value(w)
            norm = str(v) if v is not None else w
        toks.append(Tok(raw, norm, m.start(), m.end()))
    # «двадцать пять», «двадцать пятого» → 25
    for i in range(1, len(toks)):
        prev, cur = toks[i - 1], toks[i]
        pw, cw = prev.text.lower().replace("ё", "е"), cur.text.lower().replace("ё", "е")
        if pw in _TENS and (cw in _UNITS or (cw in _ORDINALS and _ORDINALS[cw] < 10)):
            prev.norm = str(_TENS[pw] + (_UNITS.get(cw) or _ORDINALS[cw]))
            cur.norm = ""
    _join_spoken_minutes(toks)
    return toks


def _join_spoken_minutes(toks: list[Tok]) -> None:
    """«в восемь тридцать» → 8:30, «в четырнадцать ноль ноль» → 14:00, «к семи пятнадцати» → 7:15."""
    def word(t: Tok) -> str:
        return t.text.lower().replace("ё", "е")

    for i in range(1, len(toks) - 1):
        hour, w = toks[i], word(toks[i])
        if word(toks[i - 1]) not in ("в", "к", "до", "с", "со") or w[0].isdigit() or not hour.norm.isdigit():
            continue
        if int(hour.norm) > 23:
            continue
        j = i + 1
        while j < len(toks) and toks[j].norm == "":  # «двадцать три» уже склеено в 23
            j += 1
        if j >= len(toks):
            continue
        mw = word(toks[j])
        minutes, used = None, []
        if mw in _TEENS or mw in _TENS:
            glued = [k for k in range(j + 1, len(toks)) if toks[k].norm == ""][:1]
            if glued and word(toks[glued[0]]) in _ORDINALS:
                continue  # «в пять двадцать пятого» — это дата, а не 5:25
            minutes, used = int(toks[j].norm), [j, *glued]
        elif mw == "ноль" and j + 1 < len(toks):
            nw = word(toks[j + 1])
            if nw == "ноль":
                minutes, used = 0, [j, j + 1]
            elif nw in _UNITS:
                minutes, used = _UNITS[nw], [j, j + 1]
        if minutes is None or minutes > 59:
            continue
        hour.norm = f"{hour.norm}:{minutes:02d}"
        for k in used:
            toks[k].norm = ""


class _Text:
    """Нормализованная строка с картой «символ → индекс токена» и маскированием съеденного."""

    def __init__(self, toks: list[Tok]):
        self.toks = toks
        parts: list[str] = []
        self.owner: list[int] = []
        for idx, t in enumerate(toks):
            if not t.norm:
                continue
            if parts:
                parts.append(" ")
                self.owner.append(-1)
            parts.append(t.norm)
            self.owner.extend([idx] * len(t.norm))
        self.chars = list("".join(parts))
        self.consumed: set[int] = set()

    @property
    def s(self) -> str:
        return "".join(self.chars)

    def consume(self, a: int, b: int) -> None:
        for i in range(a, b):
            idx = self.owner[i]
            if idx >= 0:
                self.consumed.add(idx)
                self.chars[i] = "#"
                # склеенные числительные («двадцать пятого») уходят вместе с первым словом
                j = idx + 1
                while j < len(self.toks) and self.toks[j].norm == "":
                    self.consumed.add(j)
                    j += 1


# --------------------------------------------------------------------------- результат


@dataclass
class TimeInfo:
    moment: datetime | None = None           # конкретный день/момент
    has_date: bool = False                   # день назван явно (а не выведен из одного времени)
    has_time: bool = False                   # указано время суток
    period: tuple[datetime, datetime] | None = None  # период [start, end) — «на этой неделе»
    deadline: bool = False                   # «до пятницы», «до конца недели»
    duration_min: int | None = None          # «на 2 часа»
    range_end: datetime | None = None        # явный интервал «с 13:40 до 13:50»
    error: str | None = None                 # не сохранять некорректный интервал
    tod: str | None = None                   # время суток словами («утром») — для запросов это часть дня
    tokens: list[Tok] = field(default_factory=list)
    consumed: set[int] = field(default_factory=set)

    @property
    def found(self) -> bool:
        return self.moment is not None or self.period is not None

    @property
    def phrase(self) -> str:
        return " ".join(self.tokens[i].text for i in sorted(self.consumed))

    def query_range(self) -> tuple[datetime, datetime] | None:
        """Диапазон для запросов «что у меня ...»."""
        if self.error:
            return None
        if self.range_end is not None and self.moment is not None:
            return self.moment, self.range_end
        if self.period is not None:
            return self.period
        if self.moment is None:
            return None
        day = datetime.combine(self.moment.date(), time())
        if self.tod in _TOD_RANGE:  # «что у меня завтра утром» — всё утро, а не один час
            h1, h2 = _TOD_RANGE[self.tod]
            return day + timedelta(hours=h1), day + timedelta(hours=h2)
        if self.has_time:
            return self.moment, self.moment + timedelta(hours=1)
        day = datetime.combine(self.moment.date(), time())
        return day, day + timedelta(days=1)

    def remaining_text(self, extra_consumed: set[int] | None = None) -> str:
        skip = self.consumed | (extra_consumed or set())
        return join_tokens([t for i, t in enumerate(self.tokens) if i not in skip])


def join_tokens(toks: list[Tok]) -> str:
    out = ""
    for t in toks:
        if out and not re.fullmatch(r"[,.!?;:»)]", t.text) and not out.endswith(("«", "(")):
            out += " "
        out += t.text
    return out.strip()


# --------------------------------------------------------------------------- правила

_B = r"(?<![а-яa-z0-9#])"
_E = r"(?![а-яa-z0-9])"
_PRE = r"(?:(?P<pre>в|во|на|к|ко|до|с|со|по|около|примерно)\s)?"
_MONTH = (r"(?P<mon>январ[ьяе]|феврал[ьяе]|март[ае]?|апрел[ьяе]|ма[йяе]|июн[ьяе]|июл[ьяе]|август[ае]?"
          r"|сентябр[ьяе]|октябр[ьяе]|ноябр[ьяе]|декабр[ьяе])")
_WD = (r"(?P<wd>понедельник[а-я]*|вторник[а-я]*|сред[аеуы]|четверг[а-я]*|пятниц[аеуы]|суббот[аеуы]"
       r"|воскресень[еяю])")
_MOD = r"(?P<mod>эт(?:от|ом|ой|им|а|о|у|и)|текущ\w*|следующ\w*|будущ\w*|ближайш\w*|прошл\w*|предыдущ\w*|позапрошл\w*)"
_UNIT = (r"(?P<unit>минут[ауы]?|мин|час(?:а|ов)?|сутки|суток|дн(?:я|ей)|день|недел[июья]|месяц(?:а|ев)?"
         r"|год(?:а)?|лет)")

_WD_INDEX = {"пон": 0, "вто": 1, "сре": 2, "чет": 3, "пят": 4, "суб": 5, "вос": 6}
_MONTH_INDEX = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "июн": 6, "июл": 7, "авг": 8, "сен": 9,
                "окт": 10, "ноя": 11, "дек": 12}
_TOD_DEFAULT = {"morning": (9, 0), "day": (13, 0), "evening": (19, 0), "night": (23, 0)}
_TOD_RANGE = {"morning": (5, 12), "day": (12, 17), "evening": (17, 24), "night": (21, 24)}


def _month_num(word: str) -> int:
    if word.startswith("ма") and not word.startswith("мар"):
        return 5
    return _MONTH_INDEX[word[:3]]


@dataclass
class _Parts:
    day: date | None = None
    weekday: tuple[int, str | None] | None = None
    hm: tuple[int, int] | None = None
    meridiem: str | None = None
    tod: str | None = None
    offset: timedelta | None = None
    offset_months: int = 0
    offset_has_time: bool = False
    period: tuple[datetime, datetime] | None = None
    week_anchor: date | None = None
    deadline: bool = False
    duration_min: int | None = None
    clock_range: bool = False
    error: str | None = None


def _unit_delta(unit: str, n: float) -> tuple[timedelta, int, bool]:
    """Возвращает (сдвиг, сдвиг в месяцах, затрагивает ли время суток)."""
    if unit.startswith("мин"):
        return timedelta(minutes=n), 0, True
    if unit.startswith("час"):
        return timedelta(minutes=round(n * 60)), 0, True
    if unit.startswith(("дн", "ден", "сут")):
        return timedelta(days=n), 0, False
    if unit.startswith("недел"):
        return timedelta(weeks=n), 0, False
    if unit.startswith("месяц"):
        return timedelta(), int(n), False
    return timedelta(), int(n) * 12, False  # год / лет


def _add_months(d: datetime, months: int) -> datetime:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _mod_shift(mod: str | None) -> int:
    if not mod:
        return 0
    if mod.startswith(("следующ", "будущ")):
        return 1
    if mod.startswith("позапрошл"):
        return -2
    if mod.startswith(("прошл", "предыдущ")):
        return -1
    return 0


def _pick_year(month: int, day: int, today: date, prefer: Prefer) -> date | None:
    candidates = []
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(y, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    if prefer == "future":
        future = [c for c in candidates if c >= today]
        return min(future) if future else max(candidates)
    if prefer == "past":
        past = [c for c in candidates if c <= today]
        return max(past) if past else min(candidates)
    return min(candidates, key=lambda c: abs((c - today).days))


class _Parser:
    def __init__(self, text: str, now: datetime, prefer: Prefer):
        self.now = now
        self.today = now.date()
        self.prefer = prefer
        self.toks = tokenize(text)
        self.t = _Text(self.toks)
        self.p = _Parts()

    # Каждое правило: (регулярка, обработчик). Обработчик возвращает False, если совпадение
    # оказалось ложным (тогда токены не съедаются).
    def rules(self) -> list[tuple[re.Pattern, Callable[[re.Match], bool | None]]]:
        R = lambda s: re.compile(s)  # noqa: E731
        return [
            # Интервал целиком съедается раньше отдельных часов: конец не должен затирать начало.
            (R(_B + r"(?:с|со|от)\s(?P<h>\d{1,2})(?:[:.](?P<mi>\d{2}))?"
               r"(?:\s(?P<mer>утра|дня|вечера|ночи))?\s(?:до|по|[-–—])\s"
               r"(?P<end_h>\d{1,2})(?:[:.](?P<end_mi>\d{2}))?"
               r"(?:\s(?P<end_mer>утра|дня|вечера|ночи))?" + _E), self._clock_range),
            (R(_B + r"(?P<h>\d{1,2})[:.](?P<mi>\d{2})\s?[-–—]\s?"
               r"(?P<end_h>\d{1,2})[:.](?P<end_mi>\d{2})" + _E), self._clock_range),
            (R(_B + r"через\s(?:(?P<n>\d+(?:\.5)?)\s)?" + _UNIT + _E), self._offset),
            (R(_B + r"(?:(?P<n>\d+(?:\.5)?)\s)?" + _UNIT + r"\sназад" + _E), self._ago),
            (R(_B + r"(?:в|на|за)\s(?P<dir>ближайш\w*|следующие|последн\w*|прошедш\w*)\s(?:(?P<n>\d+)\s)?"
               + _UNIT + _E), self._window),
            (R(_B + r"(?:(?P<pre>до|к|в)\s)?конц[аеуы]\s(?:" + _MOD + r"\s)?(?P<unit>недели|месяца|дня|года)" + _E),
             self._end_of),
            (R(_B + r"на\s(?P<n>\d+(?:\.5)?)\s(?P<unit>час(?:а|ов)?|минут\w*)" + _E), self._duration),
            (R(_B + _PRE + r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})" + _E), self._iso_date),
            (R(_B + _PRE + r"(?P<d>\d{1,2})(?:\sчисл[ао])?\s" + _MONTH + r"(?:\s(?P<y>\d{4})(?:\sгод[ау]?)?)?" + _E),
             self._day_month),
            (R(_B + _PRE + r"(?P<d>\d{1,2})[./](?P<m>\d{1,2})(?:[./](?P<y>\d{2,4}))?" + _E), self._dotted_date),
            (R(_B + _PRE + r"(?:" + _MOD + r"\s)?" + _WD + _E), self._weekday),
            (R(_B + _PRE + r"(?P<rel>позавчера|вчера|сегодня|послезавтра|завтра|сегодняшн\w*|завтрашн\w*"
               r"|вчерашн\w*)(?:\s(?:день|дня|числа))?" + _E), self._relative_day),
            (R(_B + _PRE + r"(?:" + _MOD + r"\s)?выходн(?:ые|ых)" + _E), self._weekend),
            (R(_B + _PRE + r"(?:" + _MOD + r"\s)?недел[еиюя]" + _E), self._week),
            (R(_B + _PRE + _MOD + r"\sмесяц[аеу]?" + _E), self._month_rel),
            (R(_B + _PRE + _MONTH + r"(?:\s(?P<y>\d{4}))?" + _E), self._month_named),
            (R(_B + _PRE + _MOD + r"\sгод[ау]?" + _E), self._year_rel),
            (R(_B + r"(?:(?:в|во|к|ко|около|до|с|со|на)\s)?(?P<h>\d{1,2})[:.](?P<mi>\d{2})"
               r"(?:\s(?P<mer>утра|дня|вечера|ночи))?" + _E), self._clock),
            (R(_B + r"(?:в|во|к|ко|около|до|с|со|на)\s(?P<h>\d{1,2})(?:\s(?:час(?:а|ов)?|ч))?"
               r"(?:\s(?P<mi>\d{1,2})\s(?:минут\w*|мин))?(?:\s(?P<mer>утра|дня|вечера|ночи))?" + _E
               + r"(?!\s(?:январ|феврал|март|апрел|ма[йяе]|июн|июл|август|сентябр|октябр|ноябр|декабр|числ"
               r"|минут|мин\b|дн|день|недел|месяц|год|лет|раз|человек|штук|рубл|процент|%))"), self._clock),
            (R(_B + r"(?P<h>\d{1,2})\s(?:час(?:а|ов)?\s)?(?P<mer>утра|дня|вечера|ночи)" + _E), self._clock),
            (R(_B + r"(?P<h>\d{1,2})\sчас(?:а|ов)" + _E), self._clock),
            (R(_B + r"(?:в\s)?(?:половин[еу]|пол)\s(?P<h>\d{1,2})(?:\s(?P<mer>утра|дня|вечера|ночи))?" + _E),
             self._half_past),
            (R(_B + r"в\sчас(?:\s(?P<mer>дня|ночи))?" + _E), self._one_oclock),
            (R(_B + r"(?:в\s)?(?P<noon>полдень|полночь)" + _E), self._noon),
            (R(_B + r"(?:(?P<tod>утром|днем|вечером|ночью)|(?:с|на|под|до|к|в|после|перед)\s(?P<tod2>утра|утро|вечер"
               r"|вечера|ночь|обед|обеда|обедом|обеду))" + _E), self._tod),
        ]

    def run(self) -> TimeInfo:
        for pattern, handler in self.rules():
            pos = 0
            while True:
                m = pattern.search(self.t.s, pos)
                if not m:
                    break
                ok = handler(m)
                if ok is False:
                    pos = m.start() + 1
                    continue
                self.t.consume(m.start(), m.end())
                pos = m.end()
        return self._resolve()

    # ---- обработчики

    def _clock_range(self, m: re.Match):
        h, end_h = int(m.group("h")), int(m.group("end_h"))
        mi, end_mi = int(m.group("mi") or 0), int(m.group("end_mi") or 0)
        if h > 23 or end_h > 23 or mi > 59 or end_mi > 59:
            self.p.error = "В интервале указано некорректное время. Назовите начало и конец, например: с 13:40 до 13:50."
            return
        exact = m.group("mi") is not None or m.group("end_mi") is not None or h > 12 or end_h > 12

        def hour(value: int, mer: str | None):
            if mer in ("дня", "вечера") and value < 12:
                return value + 12
            if mer in ("утра", "ночи") and value == 12:
                return 0
            return value + 12 if not mer and not exact and 1 <= value <= 7 else value

        mer, end_mer = m.groupdict().get("mer"), m.groupdict().get("end_mer")
        start = hour(h, mer or (end_mer if end_mer == "утра" else None)) * 60 + mi
        end = hour(end_h, end_mer) * 60 + end_mi
        if end < start and start >= 18 * 60 and end <= 12 * 60:
            end += 24 * 60  # ночная встреча, например 23:30–00:30
        if end <= start:
            self.p.error = "Конец интервала должен быть позже начала. Уточните время, например: с 13:40 до 13:50."
            return
        self.p.hm, self.p.meridiem = divmod(start, 60), "exact"
        self.p.duration_min, self.p.clock_range = end - start, True

    def _offset(self, m: re.Match):
        n = float(m.group("n") or 1)
        delta, months, has_time = _unit_delta(m.group("unit"), n)
        self.p.offset, self.p.offset_months, self.p.offset_has_time = delta, months, has_time

    def _ago(self, m: re.Match):
        n = float(m.group("n") or 1)
        delta, months, has_time = _unit_delta(m.group("unit"), n)
        self.p.offset, self.p.offset_months, self.p.offset_has_time = -delta, -months, has_time

    def _window(self, m: re.Match):
        n = float(m.group("n") or 1)
        delta, months, has_time = _unit_delta(m.group("unit"), n)
        if months:
            delta = timedelta(days=30 * months)
        past = m.group("dir").startswith(("последн", "прошедш"))
        if has_time:  # «в ближайшие 2 часа»
            self.p.period = (self.now - delta, self.now) if past else (self.now, self.now + delta)
            return
        start_today = datetime.combine(self.today, time())
        if past:  # «за последние 3 дня» — включая сегодня
            self.p.period = (start_today - delta + timedelta(days=1), self.now)
        else:     # «в ближайшие 3 дня» — сегодня и два следующих
            self.p.period = (self.now, start_today + delta)

    def _end_of(self, m: re.Match):
        unit, shift = m.group("unit"), _mod_shift(m.group("mod"))
        if unit == "недели":
            last = _week_monday(self.today) + timedelta(days=7 * shift + 6)
        elif unit == "месяца":
            first = _add_months(datetime.combine(self.today.replace(day=1), time()), shift).date()
            last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        elif unit == "года":
            last = date(self.today.year + shift, 12, 31)
        else:
            last = self.today
        self.p.day = last
        self.p.deadline = True
        self.p.period = (self.now, datetime.combine(last, time()) + timedelta(days=1))

    def _duration(self, m: re.Match):
        n = float(m.group("n"))
        if not self.p.clock_range:
            self.p.duration_min = round(n * 60) if m.group("unit").startswith("час") else round(n)

    def _set_day(self, d: date, pre: str | None):
        self.p.day = d
        if pre in ("до", "к", "ко"):
            self.p.deadline = True

    def _iso_date(self, m: re.Match):
        try:
            self._set_day(date(int(m.group("y")), int(m.group("m")), int(m.group("d"))), m.group("pre"))
        except ValueError:
            return False

    def _day_month(self, m: re.Match):
        d, mon = int(m.group("d")), _month_num(m.group("mon"))
        if m.group("y"):
            try:
                day = date(int(m.group("y")), mon, d)
            except ValueError:
                return False
        else:
            day = _pick_year(mon, d, self.today, self.prefer)
            if day is None:
                return False
        self._set_day(day, m.group("pre"))

    def _dotted_date(self, m: re.Match):
        d, mon = int(m.group("d")), int(m.group("m"))
        y = m.group("y")
        # «в 10.30» — это время, а не дата
        if not y and (m.group("pre") in ("в", "во", "к", "около", "с") or not 1 <= mon <= 12):
            return False
        if not 1 <= mon <= 12:
            return False
        if y:
            year = int(y) + (2000 if len(y) == 2 else 0)
            try:
                day = date(year, mon, d)
            except ValueError:
                return False
        else:
            day = _pick_year(mon, d, self.today, self.prefer)
            if day is None:
                return False
        self._set_day(day, m.group("pre"))

    def _weekday(self, m: re.Match):
        self.p.weekday = (_WD_INDEX[m.group("wd")[:3]], m.group("mod"))
        if m.group("pre") in ("до", "к", "ко"):
            self.p.deadline = True

    def _relative_day(self, m: re.Match):
        rel = m.group("rel")
        shift = {"позавчера": -2, "вчера": -1, "сегодня": 0, "послезавтра": 2, "завтра": 1}.get(rel)
        if shift is None:
            shift = 0 if rel.startswith("сегодня") else 1 if rel.startswith("завтра") else -1
        self._set_day(self.today + timedelta(days=shift), m.group("pre"))

    def _weekend(self, m: re.Match):
        shift = _mod_shift(m.group("mod"))
        sat = _week_monday(self.today) + timedelta(days=5 + 7 * shift)
        if shift == 0 and self.prefer == "past" and self.today < sat:
            sat -= timedelta(days=7)
        start = datetime.combine(sat, time())
        self.p.period = (start, start + timedelta(days=2))

    def _week(self, m: re.Match):
        monday = _week_monday(self.today) + timedelta(days=7 * _mod_shift(m.group("mod")))
        self.p.week_anchor = monday
        start = datetime.combine(monday, time())
        self.p.period = (start, start + timedelta(days=7))
        if m.group("pre") in ("до", "к"):
            self.p.deadline = True

    def _month_rel(self, m: re.Match):
        first = _add_months(datetime.combine(self.today.replace(day=1), time()), _mod_shift(m.group("mod")))
        self.p.period = (first, _add_months(first, 1))

    def _month_named(self, m: re.Match):
        mon = _month_num(m.group("mon"))
        if m.group("y"):
            year = int(m.group("y"))
        else:
            year = self.today.year
            if self.prefer == "future" and mon < self.today.month:
                year += 1
            elif self.prefer == "past" and mon > self.today.month:
                year -= 1
        first = datetime(year, mon, 1)
        self.p.period = (first, _add_months(first, 1))

    def _year_rel(self, m: re.Match):
        y = self.today.year + _mod_shift(m.group("mod"))
        self.p.period = (datetime(y, 1, 1), datetime(y + 1, 1, 1))

    def _clock(self, m: re.Match):
        h = int(m.group("h"))
        mi = int(m.groupdict().get("mi") or 0)
        if h > 23 or mi > 59:
            return False
        self.p.hm = (h, mi)
        self.p.meridiem = m.groupdict().get("mer")

    def _half_past(self, m: re.Match):
        h = int(m.group("h"))
        if not 1 <= h <= 24:
            return False
        self.p.hm = ((h - 1) % 24, 30)
        self.p.meridiem = m.group("mer")

    def _one_oclock(self, m: re.Match):
        self.p.hm = (1, 0)
        self.p.meridiem = m.group("mer") or "дня"

    def _noon(self, m: re.Match):
        self.p.hm = (12, 0) if m.group("noon") == "полдень" else (0, 0)
        self.p.meridiem = "exact"

    def _tod(self, m: re.Match):
        tod = m.group("tod") or m.group("tod2")
        if tod.startswith("утр"):
            self.p.tod = "morning"
        elif tod.startswith(("дн", "обед", "после")):
            self.p.tod = "day"
        elif tod.startswith("веч"):
            self.p.tod = "evening"
        else:
            self.p.tod = "night"

    # ---- сборка результата

    def _hour(self) -> tuple[int, int] | None:
        p = self.p
        if p.hm is None:
            return _TOD_DEFAULT[p.tod] if p.tod else None
        h, mi = p.hm
        mer = p.meridiem
        if mer in ("дня", "вечера") and h < 12:
            h += 12
        elif mer in ("ночи", "утра") and h == 12:
            h = 0
        elif mer is None:
            if p.tod in ("evening", "day") and h < 12:
                h += 12
            elif p.tod is None and 1 <= h <= 7:
                h += 12  # «в 3» в деловом контексте — это 15:00
        return h, mi

    def _resolve_weekday(self, wd: int, mod: str | None) -> date:
        monday = _week_monday(self.today)
        if self.p.week_anchor is not None:
            return self.p.week_anchor + timedelta(days=wd)
        if mod and not mod.startswith("ближайш"):
            return monday + timedelta(days=7 * _mod_shift(mod) + wd)
        if self.prefer == "past":
            return self.today - timedelta(days=(self.today.weekday() - wd) % 7)
        return self.today + timedelta(days=(wd - self.today.weekday()) % 7)

    def _resolve(self) -> TimeInfo:
        p = self.p
        info = TimeInfo(tokens=self.toks, consumed=set(self.t.consumed), deadline=p.deadline,
                        duration_min=p.duration_min, tod=p.tod if p.hm is None else None, error=p.error)
        if p.weekday is not None and p.day is None:  # «в среду 11 ноября» — решает явная дата
            p.day = self._resolve_weekday(*p.weekday)
            if p.week_anchor is not None:
                p.period = None  # «в пятницу на следующей неделе» — конкретный день
        hm = self._hour()

        if p.offset is not None:
            base = _add_months(self.now + p.offset, p.offset_months) if p.offset_months else self.now + p.offset
            if p.offset_has_time and hm is None:
                info.moment, info.has_date, info.has_time = base, True, True
                return info
            p.day = p.day or base.date()

        if p.day is not None:
            info.has_date = True
            if hm is not None:
                info.moment = datetime.combine(p.day, time(*hm))
                info.has_time = True
            else:
                info.moment = datetime.combine(p.day, time())
            # «до конца недели» — для запросов остаётся период, для задач — срок
            if p.period is not None and p.deadline:
                info.period = p.period
            return info

        if hm is not None:
            candidate = datetime.combine(self.today, time(*hm))
            if p.period is not None:
                # «на выходных в 10» — первый день периода
                candidate = datetime.combine(p.period[0].date(), time(*hm))
                info.has_date = True
            elif self.prefer == "future" and candidate < self.now - timedelta(minutes=1):
                candidate += timedelta(days=1)
            info.moment, info.has_time = candidate, True
            return info

        if p.period is not None:
            info.period = p.period
        return info


def parse(text: str, now: datetime, prefer: Prefer = "future") -> TimeInfo:
    """Разобрать фразу. prefer: future — для создания, past — «когда была», nearest — для запросов."""
    parser = _Parser(text, now, prefer)
    info = parser.run()
    if parser.p.clock_range and info.moment is not None and info.duration_min:
        info.range_end = info.moment + timedelta(minutes=info.duration_min)
    return info


def creation_moment(info: TimeInfo, now: datetime) -> tuple[datetime | None, bool]:
    """Момент для сохранения записи: (дата/время, указано ли время)."""
    if info.moment is not None:
        return info.moment, info.has_time
    if info.period is not None:
        start, end = info.period
        # «на следующей неделе» — ставим на первый день периода (но не в прошлое)
        day = max(start, datetime.combine(now.date(), time()))
        if day >= end:
            day = end - timedelta(days=1)
        return datetime.combine(day.date(), time()), False
    return None, False
