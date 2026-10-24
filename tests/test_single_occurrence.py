"""Изменение и удаление одного повторения серии (сейчас: вт 20.10.2026 19:59; планёрка — по понедельникам)."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import main
from app.clock import FixedClock
from app.container import build
from app.nlu.rules import RuleNLU
from app.nlu.schema import Intent, NLUContext
from tests.conftest import NOW, make_settings

nlu = RuleNLU()
ctx = NLUContext(now=NOW)


@pytest.mark.parametrize("text, intent, occurrence, when, scope", [
    ("Перенеси планёрку в понедельник на 12", Intent.UPDATE, "в понедельник", "на 12", None),
    ("Перенеси планёрку с понедельника на вторник", Intent.UPDATE, "с понедельника", "на вторник", None),
    ("Перенеси завтрашнюю зарядку на 10", Intent.UPDATE, "завтрашнюю", "на 10", None),
    ("Перенеси планёрку на среду в 11", Intent.UPDATE, None, "на среду в 11", None),
    ("Перенеси всю серию планёрок на вторник", Intent.UPDATE, None, "на вторник", "series"),
    ("Перенеси только эту на 12", Intent.UPDATE, None, "на 12", "one"),
    ("В пятницу созвона с Альфой не будет", Intent.DELETE, "В пятницу", None, "one"),
    ("Пропусти зарядку в понедельник", Intent.DELETE, "в понедельник", None, "one"),
    ("Удали все планёрки", Intent.DELETE, None, None, "series"),
    ("Отметь вчерашнюю зарядку выполненной", Intent.COMPLETE, "вчерашнюю", None, None),
])
def test_occurrence_and_scope_parsing(text, intent, occurrence, when, scope):
    cmd = nlu.parse(text, ctx)
    assert (cmd.intent, cmd.occurrence, cmd.when, cmd.scope) == (intent, occurrence, when, scope)


def test_not_happening_needs_subject():
    assert nlu.parse("Меня не будет завтра", ctx).intent != Intent.DELETE


def mondays(container, start=datetime(2026, 10, 26), end=datetime(2026, 11, 24)):
    return [(i.due_at, i.title) for i in container.service.list_period("demo", start, end, None)]


def test_ask_scope_then_delete_one(say_empty, empty_container):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    r = say_empty("Удали планёрку")
    assert r.awaiting == "scope" and "только в понедельник, 26 октября" in r.text
    r = say_empty("только эту")
    assert r.text.startswith("Удалено одно повторение")
    assert [d for d, _ in mondays(empty_container)][:2] == [datetime(2026, 11, 2, 10), datetime(2026, 11, 9, 10)]


def test_delete_named_day_without_question(say_empty, empty_container):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    r = say_empty("9 ноября планёрки не будет")
    assert r.intent == "delete" and "9 ноября" in r.text
    assert datetime(2026, 11, 9, 10) not in [d for d, _ in mondays(empty_container)]


def test_wrong_day_is_explained(say_empty):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    r = say_empty("Во вторник 27 октября планёрки не будет")
    assert "нет" in r.text and "каждый понедельник" in r.text


def test_move_one_occurrence(say_empty, empty_container):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    r = say_empty("Перенеси планёрку 2 ноября на 12")
    assert r.text.startswith("Перенесено только 2 ноября") and "Остальные повторения без изменений" in r.text
    got = mondays(empty_container)
    assert (datetime(2026, 11, 2, 12), "Планёрка") in got and datetime(2026, 11, 2, 10) not in [d for d, _ in got]
    assert datetime(2026, 11, 9, 10) in [d for d, _ in got]
    moved = [i for i in empty_container.repo.list_all("demo") if i.series_id]
    assert moved[0].original_date == date(2026, 11, 2)


def test_series_preferred_over_its_moved_copy(say_empty):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    say_empty("Перенеси планёрку 2 ноября на 12")
    r = say_empty("Перенеси планёрку на среду")
    assert r.awaiting == "scope"  # спрашивает про серию, а не «какую из двух»
    r = say_empty("всю серию")
    assert "каждую среду" in r.text


def test_pronoun_after_list_uses_shown_day(say_empty):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    say_empty("Что у меня 9 ноября?")
    r = say_empty("Перенеси её на 11")
    assert r.awaiting == "scope" and "9 ноября" in r.text
    r = say_empty("только эту")
    assert "9 ноября в 11:00" in r.text


def test_delete_series_removes_moved_copies(say_empty, empty_container):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    say_empty("Перенеси планёрку 2 ноября на 12")
    r = say_empty("Удали все планёрки")
    assert r.text.startswith("Удалена серия") and empty_container.repo.count("demo") == 0


def test_new_command_during_scope_question(say_empty):
    say_empty("Каждый понедельник в 10 у меня планёрка")
    say_empty("Удали планёрку")
    r = say_empty("Что у меня сегодня?")
    assert r.intent == "list"


def test_explicit_date_beats_weekday_name(say_empty, empty_container):
    say_empty("Каждую среду в 10 у меня планёрка")
    say_empty("В среду 11 ноября планёрки не будет")
    master = empty_container.repo.list_all("demo")[0]
    assert master.skipped_dates == [date(2026, 11, 11)]


def test_api_and_ics_for_single_occurrence():
    main.container = build(make_settings(seed=False), FixedClock(NOW))
    c = TestClient(main.app)
    series = c.post("/api/chat", json={"text": "Каждый понедельник в 10 у меня планёрка"}).json()["items"][0]
    c.post("/api/chat", json={"text": "Перенеси планёрку 2 ноября на 12"})
    assert c.delete(f"/api/items/{series['id']}?on=2026-11-09").json()["deleted"] == "occurrence"
    assert c.delete(f"/api/items/{series['id']}?on=2026-11-10").status_code == 404  # вторник — повторения нет
    body = c.get("/calendar/demo.ics").text
    assert "EXDATE;TZID=Europe/Moscow:20261102T100000" in body
    assert "EXDATE;TZID=Europe/Moscow:20261109T100000" in body
    assert "DTSTART;TZID=Europe/Moscow:20261102T120000" in body  # перенесённое повторение — отдельным событием
    assert c.delete(f"/api/items/{series['id']}").json()["deleted"] == "item"
    assert c.get("/api/items/upcoming?days=30").json() == []
