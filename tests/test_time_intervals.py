"""Границы времени, поправка без дублей и подтверждение пересечений до сохранения."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.clock import FixedClock
from app.container import build
from app.models import ItemType
from app.nlu.hybrid import HybridNLU
from app.nlu.rules import RuleNLU
from app.nlu.schema import Command, Intent
from app.timeparse.ru import parse
from tests.conftest import NOW, make_settings


@pytest.mark.parametrize("phrase,hour,minute,duration", [
    ("завтра с 13:40 до 13:50", 13, 40, 10),
    ("завтра с 13:40 по 13:50", 13, 40, 10),
    ("завтра с 13 до 14", 13, 0, 60),
    ("завтра с девяти до десяти утра", 9, 0, 60),
    ("завтра 13:40–13:50", 13, 40, 10),
    ("завтра с 13.40 до 13.50", 13, 40, 10),
    ("завтра с 23:30 до 00:30", 23, 30, 60),
])
def test_time_range_preserves_start_and_duration(phrase, hour, minute, duration):
    info = parse(phrase, NOW)
    expected = datetime(2026, 10, 21, hour, minute)
    assert info.moment == expected
    assert info.duration_min == duration
    assert info.range_end == expected + timedelta(minutes=duration)
    assert info.query_range() == (expected, info.range_end)
    assert info.has_time and not info.error


@pytest.mark.parametrize("phrase", ["завтра с 13:50 до 13:40", "завтра с 13:40 до 13:40", "завтра с 25:00 до 26:00"])
def test_invalid_interval_is_not_saved(phrase, say_empty, empty_container):
    response = say_empty("Добавь встречу с Иваном " + phrase)
    assert response.awaiting == "datetime"
    assert empty_container.repo.count("demo") == 0
    fixed = say_empty("с 13:40 до 13:50")
    assert fixed.intent == "create" and fixed.items[0].duration_min == 10
    assert fixed.items[0].due_at == datetime(2026, 10, 21, 13, 40)


def test_screenshot_phrase_and_correction_keep_one_record():
    container = build(make_settings(False), FixedClock(datetime(2026, 10, 24, 16)))
    say = lambda text: container.dialog.handle("screenshot", text)
    first = say("Напомни мне в воскресенье завтра то есть с 13:40 до 13:50 что у меня встреча")
    item = first.items[0]
    assert item.title == "Встреча" and item.type == ItemType.MEETING
    assert item.due_at == datetime(2026, 10, 25, 13, 40) and item.duration_min == 10
    assert "13:40–13:50" in first.text
    corrected = say("Нет неправильно с 13:35 до 13:50 у меня встреча")
    assert corrected.intent == "update" and corrected.items[0].id == item.id
    assert corrected.items[0].due_at == datetime(2026, 10, 25, 13, 35)
    assert corrected.items[0].duration_min == 15
    assert container.repo.count("demo") == 1


class DropsInterval:
    def parse(self, text, ctx):
        return Command(intent=Intent.CREATE, item_type=ItemType.REMINDER, title="Встреча",
                       when="завтра в 13:50", source="llm")


def test_rules_restore_interval_and_correction_when_llm_is_wrong(empty_container):
    empty_container.dialog.nlu = HybridNLU(RuleNLU(), DropsInterval(), always_llm=True)
    first = empty_container.dialog.handle("a", "Напомни завтра с 13:40 до 13:50 что у меня встреча")
    assert first.items[0].type == ItemType.MEETING
    assert first.items[0].due_at == datetime(2026, 10, 21, 13, 40)
    assert first.items[0].duration_min == 10
    second = empty_container.dialog.handle("a", "Нет неправильно с 13:30 до 13:50 у меня встреча")
    assert second.intent == "update" and second.items[0].duration_min == 20
    assert empty_container.repo.count("demo") == 1


def test_invalid_query_interval_is_clarified(say_empty):
    response = say_empty("Что у меня завтра с 13:50 до 13:40?")
    assert response.intent == "clarify" and "Конец интервала" in response.text


def test_overlap_requires_confirmation_and_adjacent_meetings_are_allowed(say_empty, empty_container):
    say_empty("Добавь встречу с Иваном завтра с 13:40 до 13:50")
    overlap = say_empty("Добавь встречу с Марией завтра с 13:45 до 14:00")
    assert overlap.awaiting == "confirm" and "13:40–13:50" in overlap.text
    assert empty_container.repo.count("demo") == 1
    say_empty("отмена")
    adjacent = say_empty("Добавь встречу с Марией завтра с 13:50 до 14:00")
    assert adjacent.intent == "create" and empty_container.repo.count("demo") == 2


def test_moving_into_overlap_does_not_write_until_confirmed(say_empty, empty_container):
    say_empty("Добавь встречу с Иваном завтра с 13:40 до 13:50")
    other = say_empty("Добавь встречу с Марией завтра с 14:00 до 14:10").items[0]
    response = say_empty("Перенеси её на завтра с 13:45 до 14:05")
    assert response.awaiting == "confirm"
    assert empty_container.service.get("demo", other.id).due_at == other.due_at
    moved = say_empty("да")
    assert moved.intent == "update"
    assert moved.items[0].due_at == datetime(2026, 10, 21, 13, 45)
    assert moved.items[0].duration_min == 20


def test_rejected_move_keeps_original_until_free_time_is_given(say_empty, empty_container):
    say_empty("Добавь встречу с Иваном завтра с 13:40 до 13:50")
    other = say_empty("Добавь встречу с Марией завтра с 14:00 до 14:10").items[0]
    say_empty("Перенеси её на завтра с 13:45 до 14:05")
    assert say_empty("нет").awaiting == "datetime"
    assert empty_container.service.get("demo", other.id).due_at == other.due_at
    moved = say_empty("с 14:30 до 14:35")
    assert moved.intent == "update" and moved.items[0].duration_min == 5
    assert moved.items[0].due_at == datetime(2026, 10, 21, 14, 30)


def test_conflict_with_a_long_meeting_started_previous_day(say_empty, empty_container):
    say_empty("Добавь встречу с командой завтра с 18:00 до 10:00")
    response = say_empty("Добавь встречу с Иваном послезавтра в 9")
    assert response.awaiting == "confirm" and empty_container.repo.count("demo") == 1


@pytest.mark.parametrize("answer", ["давай с 14:30 до 14:35", "нет, неправильно, с 14:30 до 14:35"])
def test_alternative_time_is_not_treated_as_yes_to_conflict(answer, say_empty, empty_container):
    say_empty("Добавь встречу с Иваном завтра с 13:40 до 13:50")
    say_empty("Добавь встречу с Марией завтра с 13:45 до 14:00")
    created = say_empty(answer)
    assert created.intent == "create" and created.items[0].due_at == datetime(2026, 10, 21, 14, 30)
    assert created.items[0].duration_min == 5 and empty_container.repo.count("demo") == 2


def test_alternative_move_keeps_proposed_day(say_empty):
    say_empty("Добавь встречу с Иваном послезавтра с 13:40 до 13:50")
    say_empty("Добавь встречу с Марией завтра с 14:00 до 14:10")
    assert say_empty("Перенеси её на послезавтра с 13:45 до 14:05").awaiting == "confirm"
    say_empty("нет")
    moved = say_empty("с 14:30 до 14:35")
    assert moved.items[0].due_at == datetime(2026, 10, 22, 14, 30)


def test_single_recurrence_move_checks_conflict_before_detaching(say_empty, empty_container):
    series = say_empty("Каждый понедельник с 13:40 до 13:50 у меня планёрка").items[0]
    say_empty("Добавь встречу с Иваном 2 ноября с 14:00 до 14:20")
    response = say_empty("Перенеси планёрку 2 ноября на 14:05")
    assert response.awaiting == "confirm"
    assert not empty_container.service.get("demo", series.id).skipped_dates
    assert empty_container.repo.count("demo") == 2
    moved = say_empty("да")
    assert moved.intent == "update" and moved.items[0].series_id == series.id
    assert moved.items[0].duration_min == 10


def test_api_and_calendar_expose_both_boundaries(monkeypatch):
    monkeypatch.setattr(main, "container", build(make_settings(False), FixedClock(NOW)))
    client = TestClient(main.app)
    response = client.post("/api/chat", json={"text": "Добавь встречу с Иваном завтра с 13:40 до 13:50"})
    item = response.json()["items"][0]
    assert item["due_at"] == "2026-10-21T13:40:00" and item["end_at"] == "2026-10-21T13:50:00"
    assert "13:40–13:50" in item["when"]
    assert client.get("/api/items/upcoming").json()[0]["end_at"] == item["end_at"]
    calendar = client.get("/api/calendar.ics").text
    assert "DTSTART;TZID=Europe/Moscow:20261021T134000" in calendar
    assert "DTEND;TZID=Europe/Moscow:20261021T135000" in calendar
