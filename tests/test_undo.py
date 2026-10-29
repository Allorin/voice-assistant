"""«Отмени последнее», «верни как было»: откат последнего изменения — голосом, после кнопок в плане, по шагам."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import main
from app.clock import FixedClock
from app.container import build
from app.models import ItemStatus
from app.nlu.rules import RuleNLU
from app.nlu.schema import Intent, NLUContext
from tests.conftest import NOW, make_settings


@pytest.mark.parametrize("phrase", ["отмени последнее", "Отмени последнее действие", "верни как было",
                                    "ой, отмени последнее", "откати", "верни всё обратно", "отмени это изменение"])
def test_undo_phrases(phrase):
    assert RuleNLU().parse(phrase, NLUContext(now=NOW)).intent == Intent.UNDO


@pytest.mark.parametrize("phrase", ["отмени", "отмена", "не надо", "отмени встречу с Иваном",
                                    "верни в работу задачу про билеты"])
def test_not_undo(phrase):
    assert RuleNLU().parse(phrase, NLUContext(now=NOW)).intent != Intent.UNDO


def test_undo_create(say_empty, empty_container):
    item = say_empty("добавь встречу с Олегом завтра в 12").items[0]
    reply = say_empty("отмени последнее")
    assert reply.intent == "undo" and reply.text == "Отменил добавление «Встреча с Олегом»."
    assert empty_container.service.get("demo", item.id) is None


def test_undo_delete_restores_the_same_record(say_empty, empty_container):
    item = say_empty("добавь встречу с Олегом завтра в 12").items[0]
    say_empty("удали встречу с Олегом")
    assert empty_container.service.get("demo", item.id) is None
    reply = say_empty("верни как было")
    assert reply.text.startswith("Отменил удаление «Встреча с Олегом».")
    back = empty_container.service.get("demo", item.id)
    assert back is not None and back.due_at == item.due_at and back.title == item.title


def test_undo_move_and_complete_step_by_step(say_empty, empty_container):
    item = say_empty("добавь встречу с Олегом завтра в 12").items[0]
    say_empty("перенеси её на 15")
    say_empty("отметь встречу с Олегом выполненной")
    assert empty_container.service.get("demo", item.id).status == ItemStatus.DONE

    assert say_empty("отмени последнее").text.startswith("Отменил отметку о выполнении «Встреча с Олегом»")
    assert empty_container.service.get("demo", item.id).status == ItemStatus.OPEN
    assert empty_container.service.get("demo", item.id).due_at == datetime(2026, 10, 21, 15)

    assert say_empty("отмени последнее").text.startswith("Отменил изменение «Встреча с Олегом»")
    assert empty_container.service.get("demo", item.id).due_at == datetime(2026, 10, 21, 12)

    say_empty("отмени последнее")  # добавление
    assert say_empty("отмени последнее").text == "Отменять нечего: изменений пока не было."


def test_undo_bulk_delete(say_empty, empty_container):
    say_empty("добавь встречу с Олегом завтра в 12")
    say_empty("добавь встречу с Анной завтра в 15")
    say_empty("удали все встречи на завтра")
    say_empty("да")
    assert empty_container.service.list_period("demo", datetime(2026, 10, 21), datetime(2026, 10, 22)) == []
    reply = say_empty("отмени последнее")
    assert reply.text.startswith("Отменил удаление 2 записей.")
    assert len(empty_container.service.list_period("demo", datetime(2026, 10, 21), datetime(2026, 10, 22))) == 2


def test_undo_series_occurrence(say_empty, empty_container):
    series = say_empty("каждый понедельник в 10 планёрка").items[0]
    say_empty("в понедельник планёрки не будет")
    assert empty_container.service.get("demo", series.id).skipped_dates
    say_empty("отмени последнее")
    assert empty_container.service.get("demo", series.id).skipped_dates == []


def test_undo_in_the_middle_of_a_question(say_empty, empty_container):
    item = say_empty("добавь встречу с Олегом завтра в 12").items[0]
    assert say_empty("добавь встречу с Анной").intent == "clarify"  # «На когда?»
    reply = say_empty("отмени последнее")
    assert reply.intent == "undo" and empty_container.service.get("demo", item.id) is None
    assert say_empty("в 15").intent != "create"  # вопрос про Анну снят


def test_undo_after_buttons_in_the_plan():
    main.container = build(make_settings(seed=False), FixedClock(NOW))
    c = TestClient(main.app)
    item = c.post("/api/chat", json={"text": "добавь встречу с Олегом завтра в 12", "session_id": "u"}).json()["items"][0]
    assert c.post(f"/api/items/{item['id']}/complete").json()["status"] == "done"
    reply = c.post("/api/chat", json={"text": "отмени последнее", "session_id": "u"}).json()
    assert reply["intent"] == "undo" and reply["items"][0]["status"] == "open"
    assert c.delete(f"/api/items/{item['id']}").status_code == 200
    reply = c.post("/api/chat", json={"text": "верни как было", "session_id": "u"}).json()
    assert reply["reply"].startswith("Отменил удаление «Встреча с Олегом»")
    assert c.get("/api/items/upcoming").json()[0]["id"] == item["id"]


def test_undo_is_per_user():
    main.container = build(make_settings(seed=False), FixedClock(NOW))
    c = TestClient(main.app)
    c.post("/api/chat", json={"text": "добавь встречу с Олегом завтра в 12"}, headers={"X-User-Id": "alice"})
    bob = c.post("/api/chat", json={"text": "отмени последнее"}, headers={"X-User-Id": "bob"}).json()
    assert bob["reply"] == "Отменять нечего: изменений пока не было."
    assert len(c.get("/api/items/upcoming", headers={"X-User-Id": "alice"}).json()) == 1
