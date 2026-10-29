from fastapi.testclient import TestClient

from app import main
from app.clock import FixedClock
from app.container import build
from tests.conftest import NOW, make_settings


def client() -> TestClient:
    main.container = build(make_settings(seed=False), FixedClock(NOW))
    return TestClient(main.app)


def test_chat_and_items_api():
    c = client()
    r = c.post("/api/chat", json={"text": "Добавь встречу с командой завтра в 15:00", "session_id": "a"})
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "create" and data["items"][0]["when"] == "завтра в 15:00–16:00"
    assert data["items"][0]["end_at"] == "2026-10-21T16:00:00"
    item_id = data["items"][0]["id"]

    upcoming = c.get("/api/items/upcoming").json()
    assert [i["id"] for i in upcoming] == [item_id]

    assert c.post(f"/api/items/{item_id}/complete").json()["status"] == "done"
    assert c.delete(f"/api/items/{item_id}").status_code == 200
    assert c.delete(f"/api/items/{item_id}").status_code == 404


def test_users_are_isolated():
    c = client()
    c.post("/api/chat", json={"text": "Запиши: купить молоко"}, headers={"X-User-Id": "alice"})
    assert c.get("/api/items/upcoming", headers={"X-User-Id": "bob"}).json() == []
    assert len(c.get("/api/items/search", params={"q": "молоко"}, headers={"X-User-Id": "alice"}).json()) == 1


def test_stt_not_configured():
    c = client()
    r = c.post("/api/stt", files={"file": ("a.webm", b"123", "audio/webm")})
    assert r.status_code == 501


def test_page_and_scripts_are_revalidated():
    c = client()
    for path in ("/", "/static/app.js", "/static/recorder.js"):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
        assert c.get(path, headers={"If-None-Match": r.headers["etag"]}).status_code in (200, 304)
    assert "cache-control" not in c.get("/api/status").headers


def test_icons_are_served():
    c = client()
    assert c.get("/favicon.ico").headers["content-type"] == "image/x-icon"
    assert c.get("/apple-touch-icon.png").status_code == 200
    manifest = c.get("/static/manifest.json").json()
    for icon in manifest["icons"]:
        assert c.get(icon["src"]).status_code == 200


def test_items_range_for_month_calendar():
    c = client()
    c.post("/api/chat", json={"text": "добавь встречу с Олегом 5 ноября в 12", "session_id": "r"})
    c.post("/api/chat", json={"text": "каждый понедельник в 10 планёрка", "session_id": "r"})
    items = c.get("/api/items/range", params={"start": "2026-11-01", "end": "2026-12-01"}).json()
    titles = [(i["title"], i["due_at"][:10]) for i in items]
    assert ("Встреча с Олегом", "2026-11-05") in titles
    assert [d for t, d in titles if t == "Планёрка"] == ["2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23",
                                                         "2026-11-30"]  # серия развёрнута по дням
    assert c.get("/api/items/range", params={"start": "2026-11-01", "end": "2027-02-01"}).status_code == 400
    assert c.get("/api/items/range", params={"start": "2026-11-02", "end": "2026-11-01"}).status_code == 400
