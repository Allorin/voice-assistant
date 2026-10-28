from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import main
from app.dialog import phrases as ph
from app.models import ItemType, NewItem
from app.storage.db import init_db
from app.storage.repository import ItemRepository
from tests.conftest import NOW


def test_details_dialog_saves_notes_and_lead(empty_container):
    def say(text):
        return empty_container.dialog.handle("details", text, collect_details=True)

    r = say("Добавь встречу с коллегами завтра с 13:50 до 14:20")
    assert r.awaiting == "details" and empty_container.repo.count() == 0
    r = say("В Zoom, подготовить вопросы по бюджету")
    assert r.awaiting == "remind_before" and empty_container.repo.count() == 0
    r = say("за пятнадцать минут")
    assert r.intent == "create"
    item = r.items[0]
    assert item.notes == "В Zoom, подготовить вопросы по бюджету"
    assert item.remind_before_min == 15
    assert item.remind_at == datetime(2026, 10, 21, 13, 35)
    assert "Напомню за 15 минут" in r.text
    saved = empty_container.repo.get("demo", item.id)
    assert saved.notes == item.notes and saved.remind_before_min == 15
    speech = ph.reminder_text(saved, NOW.date())
    assert "завтра в 13:50 у вас встреча с коллегами" in speech
    assert "Окончание — 14:20" in speech and "В Zoom" in speech


@pytest.mark.parametrize("answer", ["пропустить", "нет", "не надо"])
def test_optional_questions_can_be_skipped(empty_container, answer):
    def say(text):
        return empty_container.dialog.handle("skip", text, collect_details=True)
    assert say("Напомни завтра в 10 позвонить Ивану").awaiting == "details"
    assert say(answer).awaiting == "remind_before"
    r = say(answer)
    assert r.intent == "create" and not r.items[0].notes and r.items[0].remind_before_min == 0


def test_save_cancel_and_new_command_during_details(empty_container):
    def say(text):
        return empty_container.dialog.handle("s", text, collect_details=True)
    say("Добавь встречу завтра в 15")
    r = say("сохранить")
    assert r.intent == "create" and empty_container.repo.count() == 1
    say("Напомни завтра в 10 купить молоко")
    assert say("отмена").intent == "cancel" and empty_container.repo.count() == 1
    say("Напомни завтра в 11 купить билеты")
    r = say("Что у меня завтра?")  # необязательные вопросы не мешают: запись сохраняется, затем ответ на вопрос
    assert r.intent == "list" and r.text.startswith("Хорошо, напомню завтра в 11:00: «Купить билеты»")
    assert empty_container.repo.count() == 2


def test_missing_time_is_asked_before_optional_details(empty_container):
    def say(text):
        return empty_container.dialog.handle("s", text, collect_details=True)
    assert say("Напомни завтра купить молоко").awaiting == "time"
    assert say("в 10").awaiting == "details"
    assert say("сохранить").intent == "create"


@pytest.mark.parametrize(("text", "expected"), [
    ("за час", 60), ("за два часа", 120), ("за 1 час 30 минут", 90),
    ("за полчаса", 30), ("за день", 1440), ("за 7 дней", 10080),
    ("за 8 дней", None), ("за -10 минут", None), ("не знаю", None),
])
def test_lead_intervals(text, expected):
    from app.dialog.manager import DialogManager
    assert DialogManager._lead_minutes(text) == expected


def test_invalid_lead_keeps_draft(empty_container):
    def say(text):
        return empty_container.dialog.handle("s", text, collect_details=True)
    say("Напомни завтра в 10 позвонить Ивану")
    say("Не забыть договор")
    assert say("за 8 дней").awaiting == "remind_before"
    assert empty_container.repo.count() == 0
    r = say("в момент начала")
    assert r.items[0].notes == "Не забыть договор" and r.items[0].remind_before_min == 0


def test_lead_in_the_past_is_questioned(empty_container):
    def say(text):
        return empty_container.dialog.handle("s", text, collect_details=True)
    say("Напомни через 10 минут купить молоко")
    say("пропустить")
    r = say("за 15 минут")
    assert r.awaiting == "remind_before" and "уже прошёл" in r.text
    assert empty_container.repo.count() == 0
    assert say("сохранить").items[0].remind_before_min == 0


def test_new_command_interrupts_lead_question(empty_container):
    def say(text):
        return empty_container.dialog.handle("s", text, collect_details=True)
    say("Напомни завтра в 10 купить молоко")
    say("пропустить")
    r = say("Добавь задачу учиться 2 часа завтра")
    assert r.intent == "create" and r.items[0].type == ItemType.TASK
    assert "Купить молоко" in r.text.split("\n")[0]  # сначала сказано, что напоминание сохранено
    assert empty_container.repo.count() == 2


def test_lead_reply_can_start_with_remind_me(empty_container):
    def say(text):
        return empty_container.dialog.handle("s", text, collect_details=True)
    say("Напомни завтра в 10 купить молоко")
    say("пропустить")
    r = say("Напомни мне за час")
    assert r.intent == "create" and r.items[0].remind_before_min == 60


def test_due_reminders_use_alarm_time_and_do_not_fire_early(empty_container):
    svc = empty_container.service
    alarm = datetime(2026, 10, 21, 13, 35)
    meeting = svc.create("demo", NewItem(type=ItemType.MEETING, title="Встреча с коллегами",
                         due_at=alarm + timedelta(minutes=15), has_time=True, remind_before_min=15))
    svc.create("demo", NewItem(type=ItemType.REMINDER, title="Ещё рано",
               due_at=alarm + timedelta(minutes=1), has_time=True))
    svc.create("other", NewItem(type=ItemType.REMINDER, title="Другой пользователь",
               due_at=alarm, has_time=True))
    assert svc.due_reminders("demo", alarm - timedelta(minutes=1), alarm) == []
    assert [i.id for i in svc.due_reminders("demo", alarm, alarm + timedelta(seconds=1))] == [meeting.id]
    svc.complete("demo", meeting.id)
    assert svc.due_reminders("demo", alarm, alarm + timedelta(seconds=1)) == []


def test_recurring_alarm_crosses_midnight_and_detach_preserves_details(empty_container):
    svc = empty_container.service
    series = svc.create("demo", NewItem(type=ItemType.REMINDER, title="Лекарство",
                        notes="После еды", due_at=datetime(2026, 10, 21, 0, 10), has_time=True,
                        remind_before_min=30, recurrence="FREQ=DAILY"))
    items = svc.due_reminders("demo", datetime(2026, 10, 21, 23, 40), datetime(2026, 10, 21, 23, 41))
    assert len(items) == 1 and items[0].due_at == datetime(2026, 10, 22, 0, 10)
    assert items[0].notes == "После еды"
    child = svc.detach_occurrence("demo", series.id, datetime(2026, 10, 22).date(), datetime(2026, 10, 22, 9), True)
    assert child.notes == series.notes and child.remind_before_min == 30


def test_api_exposes_reminder_details_and_ics(empty_container):
    main.container = empty_container
    client = TestClient(main.app)
    payload = {"session_id": "api-details", "collect_details": True}
    assert client.post("/api/chat", json={**payload, "text": "Напомни завтра в 10 позвонить Ивану"}).json()["awaiting"] == "details"
    client.post("/api/chat", json={**payload, "text": "Уточнить дату поставки"})
    body = client.post("/api/chat", json={**payload, "text": "за 15 минут"}).json()
    item = body["items"][0]
    assert item["notes"] == "Уточнить дату поставки"
    assert item["remind_at"] == "2026-10-21T09:45:00"
    assert "завтра в 10:00" in item["reminder_text"] and item["notes"] in item["reminder_text"]
    empty_container.service.clock.fixed = datetime(2026, 10, 21, 9, 44)
    assert client.get("/api/reminders/due").json() == []
    empty_container.service.clock.fixed += timedelta(minutes=1)
    assert client.get("/api/reminders/due").json()[0]["id"] == item["id"]
    calendar = client.get("/api/calendar.ics").text.replace("\r\n ", "")
    assert "TRIGGER:-PT15M" in calendar and "Уточнить дату поставки" in calendar


def test_existing_database_migrates_without_losing_records(tmp_path):
    path = tmp_path / "old.db"
    url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE items (id INTEGER PRIMARY KEY, user_id TEXT, type TEXT, title TEXT,
            due_at DATETIME, has_time BOOLEAN, duration_min INTEGER, status TEXT,
            created_at DATETIME, updated_at DATETIME, source TEXT)"""))
        conn.execute(text("""INSERT INTO items VALUES (1, 'demo', 'reminder', 'Старая запись',
            '2026-10-21 10:00:00', 1, NULL, 'open', '2026-10-20 19:59:00', '2026-10-20 19:59:00', 'user')"""))
    engine.dispose()
    factory = init_db(url)
    repo = ItemRepository(factory)
    item = repo.get("demo", 1)
    assert item.title == "Старая запись" and item.notes == "" and item.remind_before_min == 0
    updated = repo.update("demo", 1, NOW, notes="Подробности", remind_before_min=10)
    assert updated.remind_at == datetime(2026, 10, 21, 9, 50)
    factory.kw["bind"].dispose()


def test_question_instead_of_conflict_answer_does_not_move_the_meeting(empty_container):
    def say(text):
        return empty_container.dialog.handle("s", text)
    say("Добавь встречу с дизайнером завтра в 11")
    assert say("Поставь встречу с командой завтра в 11:30").awaiting == "confirm"
    r = say("Что у меня на этой неделе?")
    assert r.intent == "list" and r.text.startswith("«Встреча с командой» не сохранено")
    assert empty_container.repo.count() == 1
