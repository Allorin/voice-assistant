"""Бизнес-логика над записями: создание, выборка за период, поиск, конфликты, изменения.

Сервис ничего не знает про речь и формулировки — только про данные.

Повторяющиеся записи хранятся одной строкой (серией) с правилом RRULE. Наружу сервис отдаёт
конкретные повторения (Item.occurrence=True) с датой и собственным статусом выполнения.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

from app.clock import Clock
from app.models import DEFAULT_MEETING_MINUTES, Item, ItemStatus, ItemType, NewItem
from app.recurrence import Recurrence
from app.search.engine import Hit, SearchEngine
from app.storage.repository import ItemRepository


def expand_types(item_type: ItemType | None) -> list[ItemType] | None:
    """«Задачи» в вопросах пользователя включают и напоминания."""
    if item_type is None:
        return None
    if item_type == ItemType.TASK:
        return [ItemType.TASK, ItemType.REMINDER]
    return [item_type]


def sort_hits(hits: list[Hit], now: datetime) -> list[Hit]:
    """Сначала релевантность (грубыми ступенями), внутри ступени — ближайшие будущие, затем недавние прошлые."""
    if not hits:
        return hits
    top = max(h.score for h in hits) or 1.0

    def key(h: Hit):
        bucket = round(h.score / top * 2)  # две ступени: «почти лучшее» и «заметно хуже»
        d = h.item.due_at
        if d is None:
            when = (2, 0.0)
        elif d >= now:
            when = (0, (d - now).total_seconds())
        else:
            when = (1, (now - d).total_seconds())
        return (-bucket, when)

    return sorted(hits, key=key)


def _filter_status(items: Iterable[Item], statuses: Iterable[ItemStatus] | None) -> list[Item]:
    if not statuses:
        return list(items)
    allowed = set(statuses)
    return [it for it in items if it.status in allowed]


class ItemService:
    def __init__(self, repo: ItemRepository, search: SearchEngine, clock: Clock):
        self.repo = repo
        self.search_engine = search
        self.clock = clock

    def now(self) -> datetime:
        return self.clock.now()

    # ------------------------------------------------------------ серии

    @staticmethod
    def occurrences(master: Item, start: datetime, end: datetime) -> list[Item]:
        """Повторения серии в [start, end) без исключённых дат (удалённых или перенесённых отдельно)."""
        rule = master.rule
        if rule is None or master.due_at is None:
            return []
        skipped = set(master.skipped_dates)
        return [master.occurrence_at(m) for m in rule.between(master.due_at, start, end) if m.date() not in skipped]

    @staticmethod
    def _iter_valid(master: Item, moment: datetime):
        rule = master.rule
        if rule is None or master.due_at is None:
            return
        skipped = set(master.skipped_dates)
        for occ in rule.iter_from(master.due_at, moment):
            if occ.date() not in skipped:
                yield occ

    @staticmethod
    def _last_valid_before(master: Item, moment: datetime) -> datetime | None:
        rule = master.rule
        if rule is None or master.due_at is None:
            return None
        skipped = set(master.skipped_dates)
        last = rule.last_before(master.due_at, moment)
        for _ in range(1000):
            if last is None or last.date() not in skipped:
                return last
            last = rule.last_before(master.due_at, last)
        return None

    def occurrence_on(self, master: Item, day: date) -> Item | None:
        """Повторение серии в указанный день (или None, если в этот день его нет)."""
        start = datetime.combine(day, time())
        occ = self.occurrences(master, start, start + timedelta(days=1))
        return occ[0] if occ else None

    def _in_range(self, user: str, start: datetime, end: datetime, types: list[ItemType] | None,
                  statuses: Iterable[ItemStatus] | None) -> list[Item]:
        """Разовые записи и повторения серий в [start, end), по времени."""
        items = self.repo.list_between(user, start, end, types, statuses, recurring=False)
        for master in self.repo.list_recurring(user, types, before=end):
            items += _filter_status(self.occurrences(master, start, end), statuses)
        return sorted(items, key=lambda it: (it.due_at, it.id))

    def representative(self, master: Item, start: datetime | None, end: datetime | None, tense: str,
                       only_open: bool = False) -> Item | None:
        """Одно повторение серии для поиска: ближайшее будущее или, для «когда была…», последнее прошедшее."""
        rule, dtstart = master.rule, master.due_at
        if rule is None or dtstart is None:
            return master
        now = self.now()
        if tense == "past" and start is None and end is not None and not only_open:
            last = self._last_valid_before(master, end)
            return master.occurrence_at(last) if last else None
        if start is not None or end is not None:
            occ = self.occurrences(master, start or dtstart, end or now + timedelta(days=3650))
            occ = [o for o in occ if not only_open or o.status == ItemStatus.OPEN]
            if not occ:
                return None
            return occ[-1] if tense == "past" else occ[0]
        if tense == "past":
            last = self._last_valid_before(master, now)
            return master.occurrence_at(last) if last else None
        if only_open:
            nxt = self.next_open(master, datetime.combine(now.date(), time()))
            if nxt is not None:
                return nxt
        else:
            first = next(self._iter_valid(master, now), None)
            if first is not None:
                return master.occurrence_at(first)
        last = self._last_valid_before(master, now) if tense == "any" else None
        return master.occurrence_at(last) if last else None

    @classmethod
    def next_open(cls, master: Item, moment: datetime) -> Item | None:
        """Ближайшее невыполненное повторение начиная с moment."""
        done = set(master.done_dates)
        for occ in cls._iter_valid(master, moment):
            if occ.date() not in done:
                return master.occurrence_at(occ)
        return None

    def nearest_day(self, master: Item) -> date | None:
        """День ближайшего повторения начиная с сегодняшнего."""
        first = next(self._iter_valid(master, datetime.combine(self.now().date(), time())), None)
        return first.date() if first else None

    def first_occurrence(self, rule: Recurrence, candidate: datetime, has_time: bool) -> datetime:
        """Первое повторение новой серии: не раньше текущего момента (или сегодняшнего дня для задач без времени)."""
        now = self.now()
        after = now if has_time else datetime.combine(now.date(), time())
        return rule.first_on_or_after(candidate, max(candidate, after)) or candidate

    # ------------------------------------------------------------ чтение

    def get(self, user: str, item_id: int) -> Item | None:
        return self.repo.get(user, item_id)

    def list_period(self, user: str, start: datetime, end: datetime, item_type: ItemType | None = None,
                    include_done: bool = True) -> list[Item]:
        statuses = None if include_done else [ItemStatus.OPEN]
        return self._in_range(user, start, end, expand_types(item_type), statuses)

    def upcoming(self, user: str, days: int = 7) -> list[Item]:
        start = datetime.combine(self.now().date(), time())
        return self._in_range(user, start, start + timedelta(days=days), None, None)

    def due_reminders(self, user: str, since: datetime, until: datetime) -> list[Item]:
        items = self._in_range(user, since, until, [ItemType.REMINDER, ItemType.MEETING], [ItemStatus.OPEN])
        return [it for it in items if it.has_time]

    def search(self, user: str, query: str, item_type: ItemType | None = None,
               period: tuple[datetime, datetime] | None = None, tense: str = "any",
               only_open: bool = False, only_done: bool = False) -> list[Hit]:
        now = self.now()
        start, end = period or (None, None)
        if tense == "past":
            end = min(end, now) if end else now
        elif tense == "future":
            start = max(start, now) if start else now
        statuses = [ItemStatus.OPEN] if only_open else [ItemStatus.DONE] if only_done else None
        types = expand_types(item_type)
        items = self.repo.list_between(user, start, end, types, statuses, recurring=False)
        if period is None and tense != "past":
            items += self.repo.list_undated(user, statuses)
        for master in self.repo.list_recurring(user, types):
            if only_done:
                if master.done_dates:
                    last = max(master.done_dates)
                    t = master.due_at.time() if master.due_at else time()
                    items.append(master.occurrence_at(datetime.combine(last, t)))
                continue
            rep = self.representative(master, start, end, tense, only_open)
            if rep is not None:
                items.append(rep)
        return sort_hits(self.search_engine.rank(query, items), now)

    def find_target(self, user: str, query: str, item_type: ItemType | None, done: bool = False) -> list[Hit]:
        """Кандидаты для «перенеси / удали / отметь / сними отметку ...»."""
        flags = {"only_done": True} if done else {"only_open": True}
        hits = self.search(user, query, item_type, **flags)
        if not hits and item_type is not None:
            hits = self.search(user, query, None, **flags)
        return hits

    def last_completed(self, user: str) -> Item | None:
        return self.repo.last_completed(user)

    def conflicts(self, user: str, start: datetime, duration_min: int | None,
                  exclude_id: int | None = None) -> list[Item]:
        end = start + timedelta(minutes=duration_min or DEFAULT_MEETING_MINUTES)
        candidates = self._in_range(user, start - timedelta(hours=12), end, [ItemType.MEETING], [ItemStatus.OPEN])
        return [c for c in candidates
                if c.id != exclude_id and c.has_time and c.due_at < end and c.end_at and c.end_at > start]

    # ------------------------------------------------------------ запись

    def create(self, user: str, data: NewItem) -> Item:
        if data.type == ItemType.MEETING and data.has_time and not data.duration_min:
            data = data.model_copy(update={"duration_min": DEFAULT_MEETING_MINUTES})
        return self.repo.add(user, data, self.now())

    def reschedule(self, user: str, item_id: int, due_at: datetime | None, has_time: bool) -> Item | None:
        item = self.repo.get(user, item_id)
        if item is None:
            return None
        rule = item.rule
        if rule is not None and due_at is not None:
            # серия: «перенеси планёрку на среду в 11» меняет день недели и время всех будущих повторений
            new_rule = rule.moved_to(due_at, item.due_at)
            first = self.first_occurrence(new_rule, due_at, has_time)
            return self.repo.update(user, item_id, self.now(), due_at=first, has_time=has_time,
                                    recurrence=new_rule.to_rrule())
        return self.repo.update(user, item_id, self.now(), due_at=due_at, has_time=has_time)

    def rename(self, user: str, item_id: int, title: str) -> Item | None:
        return self.repo.update(user, item_id, self.now(), title=title)

    def _pick_open_day(self, item: Item) -> date | None:
        """Какое повторение отметить, если день не назван: самое раннее невыполненное за последнюю неделю
        по сегодняшний день включительно, иначе ближайшее будущее."""
        today = self.now().date()
        start = datetime.combine(today - timedelta(days=7), time())
        for occ in self.occurrences(item, start, datetime.combine(today + timedelta(days=1), time())):
            if occ.status == ItemStatus.OPEN:
                return occ.due_at.date()
        nxt = self.next_open(item, datetime.combine(today + timedelta(days=1), time()))
        return nxt.due_at.date() if nxt else None

    def _occurrence_on(self, item: Item, day: date) -> Item:
        t = item.due_at.time() if item.due_at else time()
        return item.occurrence_at(datetime.combine(day, t))

    def complete(self, user: str, item_id: int, on: date | None = None) -> Item | None:
        """Отметить выполненной. Для серии — одно повторение (on или подходящее по смыслу)."""
        item = self.repo.get(user, item_id)
        if item is None:
            return None
        if item.rule is None:
            return self.repo.update(user, item_id, self.now(), status=ItemStatus.DONE)
        day = on or self._pick_open_day(item)
        if day is None:
            return item
        updated = self.repo.update(user, item_id, self.now(), done_dates=[*item.done_dates, day])
        return self._occurrence_on(updated, day)

    def reopen(self, user: str, item_id: int, on: date | None = None) -> Item | None:
        """Снять отметку о выполнении. Для серии — с повторения on или с последнего отмеченного."""
        item = self.repo.get(user, item_id)
        if item is None:
            return None
        if item.rule is None:
            return self.repo.update(user, item_id, self.now(), status=ItemStatus.OPEN)
        day = on or (max(item.done_dates) if item.done_dates else None)
        if day is None:
            return item
        updated = self.repo.update(user, item_id, self.now(), done_dates=[d for d in item.done_dates if d != day])
        return self._occurrence_on(updated, day)

    def skip_occurrence(self, user: str, item_id: int, day: date) -> Item | None:
        """Удалить одно повторение серии (в .ics это EXDATE). Возвращает удалённое повторение."""
        master = self.repo.get(user, item_id)
        if master is None or master.rule is None:
            return None
        occ = self.occurrence_on(master, day)
        if occ is None:
            return None
        self.repo.update(user, item_id, self.now(), skipped_dates=[*master.skipped_dates, day])
        return occ

    def detach_occurrence(self, user: str, item_id: int, day: date, due_at: datetime, has_time: bool,
                          title: str | None = None) -> Item | None:
        """Перенести (или переименовать) одно повторение: дата исключается из серии,
        а вместо неё создаётся отдельная запись, связанная с серией."""
        master = self.repo.get(user, item_id)
        if master is None or master.rule is None or self.occurrence_on(master, day) is None:
            return None
        self.repo.update(user, item_id, self.now(), skipped_dates=[*master.skipped_dates, day])
        status = ItemStatus.DONE if day in master.done_dates else ItemStatus.OPEN
        return self.repo.add(user, NewItem(
            type=master.type, title=title or master.title, due_at=due_at, has_time=has_time,
            duration_min=master.duration_min, status=status, series_id=master.id, original_date=day,
        ), self.now())

    def delete(self, user: str, item_id: int) -> bool:
        item = self.repo.get(user, item_id)
        if item is not None and item.rule is not None:
            self.repo.delete_series_children(user, item_id)  # вместе с серией уходят и её перенесённые повторения
        return self.repo.delete(user, item_id)
