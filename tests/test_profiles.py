"""Несколько пользователей: профили по X-User-Id, изоляция записей, гостевые профили публичного стенда."""
from dataclasses import replace
from urllib.parse import quote

from fastapi.testclient import TestClient

from app import main
from app.clock import FixedClock
from app.container import build
from tests.conftest import NOW, make_settings


def client(**settings) -> TestClient:
    main.container = build(replace(make_settings(seed=True), **settings), FixedClock(NOW))
    return TestClient(main.app)


def as_user(user: str) -> dict:
    return {"X-User-Id": quote(user)}  # так кодирует браузер (encodeURIComponent)


def test_profiles_are_isolated_and_start_empty_locally():
    c = client()
    alice = as_user("Алиса")
    c.post("/api/chat", json={"text": "Напомни завтра в 10 позвонить Ивану", "session_id": "a"}, headers=alice)
    assert [i["title"] for i in c.get("/api/items/upcoming", headers=alice).json()] == ["Позвонить Ивану"]
    assert c.get("/api/items/upcoming", headers=as_user("Боб")).json() == []
    assert c.get("/api/status").json()["items"] == 1000  # демо-профиль по умолчанию не изменился
    status = c.get("/api/status", headers=alice).json()
    assert status["user"] == "Алиса" and status["items"] == 1 and not status["public_demo"]
    assert status["calendar_path"] == f"/calendar/{quote('Алиса')}.ics"
    ics = c.get(status["calendar_path"]).text
    assert "Позвонить Ивану" in ics and ics.count("BEGIN:VEVENT") == 1


def test_public_demo_gives_each_guest_own_copy_once(tmp_path):
    settings = replace(make_settings(seed=True), public_demo=True, database_url=f"sqlite:///{tmp_path / 'a.db'}")
    main.container = build(settings, FixedClock(NOW))
    c = TestClient(main.app)
    guest = as_user("гость-1a2b")
    status = c.get("/api/status", headers=guest).json()
    assert status["public_demo"] and status["items"] == 1000
    for item in c.get("/api/items/upcoming", params={"days": 60}, headers=guest).json():
        c.delete(f"/api/items/{item['id']}", params={"on": item["due_at"][:10]}, headers=guest)
    left = c.get("/api/status", headers=guest).json()["items"]
    assert left < 1000
    assert c.get("/api/status", headers=as_user("гость-3c4d")).json()["items"] == 1000
    assert c.get("/api/status").json()["items"] == 1000  # общий демо-профиль не тронут

    main.container = build(settings, FixedClock(NOW))  # перезапуск: та же база, новый процесс
    assert c.get("/api/status", headers=guest).json()["items"] == left  # повторно демо-записи не выдаются


def test_bad_profile_name_is_rejected():
    c = client()
    assert c.get("/api/status", headers=as_user("../etc")).status_code == 400
    assert c.get("/api/status", headers=as_user("x" * 65)).status_code == 400
