"""Государственные праздники РФ, переносы выходных и вопросы о них. Сейчас в тестах — вторник, 20 октября 2026."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import holidays as hol
from app import main
from app.clock import FixedClock
from app.container import build
from tests.conftest import NOW, make_settings


def working_days(start: date, end: date) -> int:
    return sum(not hol.is_day_off(start + timedelta(days=i)) for i in range((end - start).days))


def test_production_calendar_counts():
    """Сверка с производственным календарём: 247 рабочих дней в 2026 и 2027, февраль и ноябрь 2027."""
    assert working_days(date(2026, 1, 1), date(2027, 1, 1)) == 247
    assert working_days(date(2027, 1, 1), date(2028, 1, 1)) == 247
    assert working_days(date(2027, 2, 1), date(2027, 3, 1)) == 19
    assert working_days(date(2027, 11, 1), date(2027, 12, 1)) == 20


@pytest.mark.parametrize(("day", "kind"), [
    (date(2026, 11, 4), hol.HOLIDAY),
    (date(2026, 1, 9), hol.DAY_OFF),    # перенос с 3 января по постановлению
    (date(2026, 12, 31), hol.DAY_OFF),  # перенос с 4 января
    (date(2026, 3, 9), hol.DAY_OFF),    # 8 марта в воскресенье — автоматический перенос
    (date(2026, 5, 11), hol.DAY_OFF),   # 9 мая в субботу
    (date(2027, 2, 20), hol.WORKDAY),   # рабочая суббота
    (date(2027, 2, 22), hol.DAY_OFF),
    (date(2027, 11, 5), hol.DAY_OFF),
    (date(2028, 11, 6), hol.DAY_OFF),   # года без постановления: автоматический перенос с субботы 4 ноября
])
def test_special_days(day, kind):
    assert hol.holiday_on(day).kind == kind
    assert hol.is_day_off(day) == (kind != hol.WORKDAY)


def test_regular_days():
    assert hol.holiday_on(date(2026, 10, 20)) is None and not hol.is_day_off(date(2026, 10, 20))
    assert hol.is_day_off(date(2026, 10, 24))  # суббота


def test_runs_merge_consecutive_days():
    january = [h for h in hol.between(date(2026, 1, 1), date(2026, 2, 1))]
    assert hol.runs(january)[0] == (date(2026, 1, 1), date(2026, 1, 6), "Новогодние каникулы")
    assert [name for _, _, name in hol.runs(january)][1:] == [
        "Рождество Христово", "Новогодние каникулы", "Выходной (перенос с 3 января)"]


def test_api_holidays():
    main.container = build(make_settings(seed=False), FixedClock(NOW))
    c = TestClient(main.app)
    rows = c.get("/api/holidays", params={"start": "2026-11-01", "end": "2026-12-01"}).json()
    assert rows == [{"date": "2026-11-04", "name": "День народного единства", "kind": "holiday"}]
    assert c.get("/api/holidays", params={"start": "2026-11-01", "end": "2026-10-01"}).status_code == 400


@pytest.mark.parametrize(("question", "expected"), [
    ("какие праздники в ноябре", "• 4 ноября — День народного единства"),
    ("4 ноября выходной?", "4 ноября — нерабочий день: День народного единства."),
    ("когда ближайший праздник", "Ближайший праздник — 4 ноября: День народного единства."),
    ("праздники в январе", "• 8 января 2027 — Новогодние каникулы"),  # в 2027 январские выходные перенесены на осень
    ("20 февраля 2027 выходной?", "рабочая суббота"),
    ("какие праздники были в мае", "• 11 мая — Выходной (перенос с 9 мая)"),
])
def test_holiday_questions(say_empty, question, expected):
    reply = say_empty(question)
    assert reply.intent == "list" and expected in reply.text, reply.text


def test_records_about_holidays_are_still_records(say_empty):
    say_empty("добавь задачу купить подарки к празднику")
    reply = say_empty("найди задачу про праздник")
    assert reply.intent == "search" and reply.items[0].title == "Купить подарки к празднику"


def test_day_list_mentions_the_holiday(say_empty):
    say_empty("добавь встречу с Олегом 4 ноября в 12")
    reply = say_empty("что у меня 4 ноября")
    assert "Праздник: 4 ноября — День народного единства." in reply.text
    assert "День народного единства" in reply.speech
