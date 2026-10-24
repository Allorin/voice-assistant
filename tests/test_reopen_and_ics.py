"""Снятие отметки о выполнении и экспорт календаря .ics."""
from fastapi.testclient import TestClient

from app import main
from app.clock import FixedClock
from app.container import build
from app.models import ItemStatus
from app.nlu.rules import RuleNLU
from app.nlu.schema import Intent, NLUContext
from tests.conftest import NOW, make_settings

nlu = RuleNLU()
ctx = NLUContext(now=NOW)


def test_reopen_phrases():
    for text, target_last in [("Сними отметку с задачи про билеты", False), ("Сними галочку", True),
                              ("Верни её в работу", True), ("Я случайно отметил задачу про акт", False),
                              ("Отметь задачу купить билеты невыполненной", False)]:
        cmd = nlu.parse(text, ctx)
        assert cmd.intent == Intent.REOPEN, text
        assert cmd.target_last == target_last, text


def test_status_questions_are_lists():
    assert nlu.parse("Какие задачи не выполнены?", ctx).status == "open"
    cmd = nlu.parse("Что я уже сделал на этой неделе", ctx)
    assert cmd.intent == Intent.LIST and cmd.status == "done"


def test_reopen_by_voice(say_empty, empty_container):
    say_empty("Поставь задачу купить билеты до конца недели")
    say_empty("Отметь задачу купить билеты выполненной")
    say_empty("Что у меня сегодня?")  # контекст сменился — «сними отметку» всё равно найдёт последнюю отмеченную
    r = say_empty("Сними отметку")
    assert r.intent == "reopen" and "Купить билеты" in r.text
    assert empty_container.repo.list_all("demo")[0].status == ItemStatus.OPEN


def test_reopen_by_query(say_empty):
    say_empty("Поставь задачу купить билеты до конца недели")
    say_empty("Отметь задачу купить билеты выполненной")
    r = say_empty("Верни в работу задачу про билеты")
    assert r.intent == "reopen" and r.items[0].status == ItemStatus.OPEN


def client() -> TestClient:
    main.container = build(make_settings(seed=False), FixedClock(NOW))
    return TestClient(main.app)


def test_api_toggle_checkbox_for_single_and_series():
    c = client()
    one = c.post("/api/chat", json={"text": "Запиши: купить молоко завтра"}).json()["items"][0]
    assert c.post(f"/api/items/{one['id']}/complete").json()["status"] == "done"
    assert c.post(f"/api/items/{one['id']}/reopen").json()["status"] == "open"

    series = c.post("/api/chat", json={"text": "Каждый день в 9 утра зарядка"}).json()["items"][0]
    assert series["recurrence_text"] == "каждый день" and series["when"] == "каждый день в 9:00"
    done = c.post(f"/api/items/{series['id']}/complete?on=2026-10-22").json()
    assert done["status"] == "done" and done["due_at"].startswith("2026-10-22")
    week = {i["due_at"][:10]: i["status"] for i in c.get("/api/items/upcoming").json() if i["id"] == series["id"]}
    assert week["2026-10-22"] == "done" and week["2026-10-23"] == "open"
    c.post(f"/api/items/{series['id']}/reopen?on=2026-10-22")
    week = {i["due_at"][:10]: i["status"] for i in c.get("/api/items/upcoming").json() if i["id"] == series["id"]}
    assert week["2026-10-22"] == "open"


def test_calendar_export():
    c = client()
    c.post("/api/chat", json={"text": "Каждый понедельник в 10 у меня планёрка"})
    c.post("/api/chat", json={"text": "Поставь задачу купить билеты, молоко; хлеб до конца недели"})
    r = c.get("/calendar/demo.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert body.count("BEGIN:VEVENT") == 2
    assert "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO" in body
    assert "DTSTART;TZID=Europe/Moscow:20261026T100000" in body
    assert "DTSTART;VALUE=DATE:20261025" in body
    assert "билеты\\, молоко\\; хлеб" in body
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    assert c.get("/calendar/other.ics").text.count("BEGIN:VEVENT") == 0
    assert c.get("/calendar/..%2Fetc.ics").status_code in (404, 422)
    assert "attachment" in c.get("/api/calendar.ics").headers["content-disposition"]
