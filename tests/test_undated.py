"""Задачи без срока видны в плане отдельным блоком."""
from fastapi.testclient import TestClient

from app import main
from app.clock import FixedClock
from app.container import build
from tests.conftest import NOW, make_settings


def test_undated_tasks_endpoint():
    main.container = build(make_settings(seed=False), FixedClock(NOW))
    c = TestClient(main.app)
    c.post("/api/chat", json={"text": "Запиши купить корм коту"})
    c.post("/api/chat", json={"text": "Запиши починить кран"})
    c.post("/api/chat", json={"text": "Напомни завтра в 10 позвонить Ивану"})
    items = c.get("/api/items/undated").json()
    assert [i["title"] for i in items] == ["Починить кран", "Купить корм коту"]  # новые сверху
    assert all(i["due_at"] is None and i["when"] for i in items)
    assert all(i["due_at"] for i in c.get("/api/items/upcoming").json())

    c.post(f"/api/items/{items[0]['id']}/complete")
    after = c.get("/api/items/undated").json()
    assert [(i["title"], i["status"]) for i in after] == [("Купить корм коту", "open"), ("Починить кран", "done")]
    assert c.get("/api/items/undated", headers={"X-User-Id": "other"}).json() == []
