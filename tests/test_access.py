"""Общий код доступа к стенду (ACCESS_CODE)."""
from dataclasses import replace
from urllib.parse import quote

from fastapi.testclient import TestClient

from app import main
from app.access import AccessGuard
from app.clock import FixedClock
from app.container import build
from tests.conftest import NOW, make_settings


def client(code: str) -> TestClient:
    main.container = build(replace(make_settings(seed=False), access_code=code), FixedClock(NOW))
    return TestClient(main.app)


def test_api_needs_code_but_page_health_and_calendar_do_not():
    c = client("секрет-42")
    r = c.get("/api/status")
    assert r.status_code == 401 and r.json()["access_code_required"]
    assert c.post("/api/chat", json={"text": "Что у меня сегодня?"}).status_code == 401
    assert c.get("/api/status", headers={"X-Access-Code": "neverno"}).status_code == 401
    ok = {"X-Access-Code": quote("секрет-42")}  # кириллица в заголовке приходит закодированной
    assert c.get("/api/status", headers=ok).status_code == 200
    assert c.post("/api/chat", json={"text": "Запиши купить хлеб завтра"}, headers=ok).json()["intent"] == "create"
    assert c.get("/").status_code == 200 and c.get("/health").status_code == 200
    assert c.get("/static/app.js").status_code == 200
    # подписка календаря не умеет слать заголовки: вместо кода — секретная ссылка
    assert c.get("/calendar/demo.ics").status_code == 404
    assert c.get("/calendar/demo.ics?token=0123456789abcdef01234567").status_code == 404
    path = c.get("/api/status", headers=ok).json()["calendar_path"]
    assert "?token=" in path and c.get(path).status_code == 200
    assert "Купить хлеб" in c.get(path).text
    other = c.get("/api/status", headers={**ok, "X-User-Id": "guest2"}).json()["calendar_path"]
    assert other.split("token=")[1] != path.split("token=")[1]  # у каждого профиля своя ссылка


def test_without_code_everything_is_open():
    c = client("")
    assert c.get("/api/status").json()["calendar_path"] == "/calendar/demo.ics"
    assert c.get("/calendar/demo.ics").status_code == 200


def test_guessing_is_rate_limited():
    clock = [0.0]
    guard = AccessGuard("code", clock=lambda: clock[0])
    for _ in range(AccessGuard.MAX_FAILURES):
        assert guard.check("1.2.3.4", "wrong") == 401
    assert guard.check("1.2.3.4", "code") == 429      # даже верный код ждёт конца окна
    assert guard.check("5.6.7.8", "code") == 200      # другие адреса не затронуты
    assert guard.check("1.2.3.4", None) == 429
    clock[0] += AccessGuard.WINDOW_SECONDS + 1
    assert guard.check("1.2.3.4", "code") == 200
    assert guard.check("9.9.9.9", None) == 401 and guard.check("9.9.9.9", "code") == 200
