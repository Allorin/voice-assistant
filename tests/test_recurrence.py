"""Повторяющиеся записи: разбор фраз, правило RRULE, сценарии диалога."""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, text

from app.models import ItemStatus, ItemType
from app.recurrence import Recurrence
from app.storage.db import init_db
from app.timeparse.repeat import parse_repeat


@pytest.mark.parametrize("phrase, rrule, rest", [
    ("Каждый понедельник в 10 у меня планёрка", "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO", "в 10 у меня планёрка"),
    ("Напоминай каждый день в 9 пить воду", "FREQ=DAILY;INTERVAL=1", "Напоминай в 9 пить воду"),
    ("По будням в 8:30 зарядка", "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU,WE,TH,FR", "в 8:30 зарядка"),
    ("по выходным бегать", "FREQ=WEEKLY;INTERVAL=1;BYDAY=SA,SU", "бегать"),
    ("по вторникам и четвергам в 19 бассейн", "FREQ=WEEKLY;INTERVAL=1;BYDAY=TU,TH", "в 19 бассейн"),
    ("каждую среду и пятницу в 11 стендап", "FREQ=WEEKLY;INTERVAL=1;BYDAY=WE,FR", "в 11 стендап"),
    ("Каждые 2 недели по пятницам созвон", "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR", "созвон"),
    ("каждые три дня поливать цветы", "FREQ=DAILY;INTERVAL=3", "поливать цветы"),
    ("оплатить интернет 15 числа каждого месяца", "FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=15", "оплатить интернет"),
    ("каждое 25 число платить за квартиру", "FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=25", "платить за квартиру"),
    ("ежемесячно отправлять отчёт", "FREQ=MONTHLY;INTERVAL=1", "отправлять отчёт"),
    ("каждый год поздравить маму 5 марта", "FREQ=YEARLY;INTERVAL=1", "поздравить маму 5 марта"),
])
def test_parse_repeat(phrase, rrule, rest):
    info = parse_repeat(phrase)
    assert info.rule.to_rrule() == rrule
    assert info.rest == rest


@pytest.mark.parametrize("phrase", ["по пятницу работать", "Позвонить Ивану завтра в 10", "Что у меня в понедельник?"])
def test_not_a_repeat(phrase):
    assert parse_repeat(phrase).rule is None


def test_default_time_of_day():
    assert parse_repeat("Каждое утро выпить таблетку").default_time.hour == 9


def test_rrule_roundtrip_and_expand():
    rule = Recurrence.parse("FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE")
    assert rule.to_rrule() == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE"
    occ = rule.between(datetime(2026, 10, 26, 10), datetime(2026, 10, 26), datetime(2026, 11, 2))
    assert occ == [datetime(2026, 10, 26, 10), datetime(2026, 10, 28, 10)]


def test_move_weekly_series_to_other_day():
    rule = Recurrence("WEEKLY", 1, (0,))
    moved = rule.moved_to(datetime(2026, 10, 27, 11), datetime(2026, 10, 26, 10))
    assert moved.byday == (1,)


# --------------------------------------------------------------------- диалог (сейчас: вт 20.10.2026 19:59)

def test_create_weekly_meeting_and_list(say_empty, empty_container):
    r = say_empty("Каждый понедельник в 10 у меня планёрка")
    item = r.items[0]
    assert item.type == ItemType.MEETING and item.recurrence == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO"
    assert item.due_at == datetime(2026, 10, 26, 10, 0)
    assert "каждый понедельник в 10:00" in r.text
    r = say_empty("Что запланировано на следующую неделю?")
    assert [i.due_at for i in r.items] == [datetime(2026, 10, 26, 10, 0)]
    month = empty_container.service.list_period("demo", datetime(2026, 11, 1), datetime(2026, 12, 1))
    assert [i.due_at.day for i in month] == [2, 9, 16, 23, 30]


def test_meeting_series_asks_time(say_empty):
    r = say_empty("Каждую среду встреча с командой")
    assert r.awaiting == "time"
    r = say_empty("в 12")
    assert r.items[0].due_at == datetime(2026, 10, 21, 12, 0) and r.items[0].recurrence


def test_daily_reminder_first_time_tomorrow(say_empty):
    r = say_empty("Напоминай каждый день в 9 пить воду")  # сейчас 19:59 — сегодняшние 9:00 уже прошли
    assert r.items[0].type == ItemType.REMINDER and r.items[0].due_at == datetime(2026, 10, 21, 9, 0)


def test_complete_and_reopen_one_occurrence(say_empty, empty_container):
    say_empty("Каждый день в 9 утра зарядка")
    r = say_empty("Отметь зарядку выполненной")
    assert r.intent == "complete" and "Следующий раз" in r.text
    master = empty_container.repo.list_all("demo")[0]
    assert master.done_dates == [date(2026, 10, 21)]
    days = empty_container.service.list_period("demo", datetime(2026, 10, 21), datetime(2026, 10, 23))
    assert [d.status for d in days] == [ItemStatus.DONE, ItemStatus.OPEN]
    r = say_empty("Сними отметку")
    assert r.intent == "reopen"
    assert empty_container.repo.list_all("demo")[0].done_dates == []


def test_reschedule_series_changes_weekday(say_empty):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    r = say_empty("Перенеси планёрку на среду в 11")
    assert r.awaiting == "scope" and "или всю серию" in r.text
    r = say_empty("всю серию")
    item = r.items[0]
    assert item.recurrence == "FREQ=WEEKLY;INTERVAL=1;BYDAY=WE"
    assert item.due_at == datetime(2026, 10, 21, 11, 0)
    assert "каждую среду в 11:00" in r.text


def test_delete_series(say_empty, empty_container):
    say_empty("По будням в 8:30 зарядка")
    r = say_empty("Удали всю серию зарядок")
    assert r.text.startswith("Удалена серия") and empty_container.repo.count("demo") == 0


def test_series_conflict_and_reminders(empty_container, say_empty):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    r = say_empty("Добавь встречу с Иваном 2 ноября в 10:30")
    assert r.awaiting == "confirm" and "Планёрка" in r.text
    due = empty_container.service.due_reminders("demo", datetime(2026, 11, 9, 9, 58), datetime(2026, 11, 9, 10, 1))
    assert [d.due_at for d in due] == [datetime(2026, 11, 9, 10, 0)]


def test_search_finds_next_occurrence(say_empty):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    r = say_empty("Когда планёрка?")
    assert r.items[0].due_at == datetime(2026, 10, 26, 10, 0)


def test_migration_adds_columns(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, user_id VARCHAR(64), type VARCHAR(16), "
                          "title VARCHAR(500), due_at DATETIME, has_time BOOLEAN, duration_min INTEGER, "
                          "status VARCHAR(16), created_at DATETIME, updated_at DATETIME, source VARCHAR(16))"))
    init_db(url)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(items)"))}
    assert {"recurrence", "done_dates"} <= cols
