"""HTTP API (FastAPI) и раздача веб-интерфейса."""
from __future__ import annotations

import logging
import os
import threading
import webbrowser
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from fastapi import FastAPI, File, Header, HTTPException, Path, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import BASE_DIR, get_settings
from app.container import Container, build
from app.dialog import phrases as ph
from app.models import Item
from app.services.ics import build_calendar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC_DIR = BASE_DIR / "app" / "static"

container: Container | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global container
    container = build(get_settings())
    url = os.getenv("OPEN_BROWSER_URL")
    if url:  # start.bat: открыть страницу, только когда сервер точно поднялся
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    try:
        yield
    finally:
        if container.nlu.llm is not None:
            container.nlu.llm.close()


app = FastAPI(title="Голосовой ежедневник", lifespan=lifespan)


def c() -> Container:
    assert container is not None
    return container


def _user(x_user_id: str | None) -> str:
    return (x_user_id or "").strip()[:64] or c().settings.default_user


class ItemOut(BaseModel):
    id: int
    type: str
    type_label: str
    title: str
    notes: str = ""
    remind_before_min: int = 0
    remind_at: datetime | None = None
    reminder_text: str = ""
    due_at: datetime | None
    end_at: datetime | None = None
    has_time: bool
    when: str
    status: str
    duration_min: int | None
    recurrence: str | None = None        # RRULE серии
    recurrence_text: str | None = None   # «каждый понедельник»
    occurrence: bool = False             # это одно повторение серии (отметка ставится на его дату)
    series_id: int | None = None         # перенесённое отдельно повторение: id его серии


def item_out(item: Item) -> ItemOut:
    today = c().service.now().date()
    series = bool(item.recurrence)
    when = ph.series_when(item) if series and not item.occurrence else \
        ph.when_phrase(item.due_at, item.has_time, today, item.duration_min)
    return ItemOut(
        id=item.id, type=item.type.value, type_label=ph.type_label(item.type), title=item.title,
        notes=item.notes, remind_before_min=item.remind_before_min, remind_at=item.remind_at,
        reminder_text=ph.reminder_text(item, today),
        due_at=item.due_at, has_time=item.has_time, when=when, status=item.status.value,
        end_at=item.end_at if item.duration_min else None,
        duration_min=item.duration_min, recurrence=item.recurrence,
        recurrence_text=ph.recurrence_text(item.rule, item.due_at) if series else None,
        occurrence=item.occurrence, series_id=item.series_id,
    )


class ChatIn(BaseModel):
    text: str = Field(max_length=1000)
    session_id: str = Field(default="default", max_length=100)
    collect_details: bool = False


class ChatOut(BaseModel):
    reply: str
    speech: str
    intent: str
    awaiting: str | None
    nlu: str
    items: list[ItemOut]
    changed: list[int]


@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn, x_user_id: str | None = Header(default=None)):
    r = c().dialog.handle(body.session_id, body.text, _user(x_user_id), collect_details=body.collect_details)
    return ChatOut(reply=r.text, speech=r.speech or r.text, intent=r.intent, awaiting=r.awaiting, nlu=r.nlu,
                   items=[item_out(i) for i in r.items[:30]], changed=r.changed)


@app.post("/api/session/reset")
def reset_session(body: ChatIn):
    c().dialog.sessions.reset(body.session_id)
    return {"ok": True}


@app.get("/api/items/upcoming", response_model=list[ItemOut])
def upcoming(days: int = 7, x_user_id: str | None = Header(default=None)):
    return [item_out(i) for i in c().service.upcoming(_user(x_user_id), max(1, min(days, 60)))]


@app.get("/api/items/search", response_model=list[ItemOut])
def search(q: str, limit: int = 20, x_user_id: str | None = Header(default=None)):
    hits = c().service.search(_user(x_user_id), q)
    return [item_out(h.item) for h in hits[: max(1, min(limit, 100))]]


@app.post("/api/items/{item_id}/complete", response_model=ItemOut)
def complete(item_id: int, on: date | None = None, x_user_id: str | None = Header(default=None)):
    """Отметить выполненной. Для серии on — дата повторения (по умолчанию — ближайшее невыполненное)."""
    item = c().service.complete(_user(x_user_id), item_id, on)
    if item is None:
        raise HTTPException(404, "Запись не найдена")
    return item_out(item)


@app.post("/api/items/{item_id}/reopen", response_model=ItemOut)
def reopen(item_id: int, on: date | None = None, x_user_id: str | None = Header(default=None)):
    """Снять отметку о выполнении (например, если галочку поставили случайно)."""
    item = c().service.reopen(_user(x_user_id), item_id, on)
    if item is None:
        raise HTTPException(404, "Запись не найдена")
    return item_out(item)


@app.delete("/api/items/{item_id}")
def delete(item_id: int, on: date | None = None, x_user_id: str | None = Header(default=None)):
    """Удалить запись. Для серии с on — только повторение в этот день, без on — всю серию."""
    user = _user(x_user_id)
    if on is not None:
        item = c().service.get(user, item_id)
        if item is not None and item.rule is not None:
            if c().service.skip_occurrence(user, item_id, on) is None:
                raise HTTPException(404, "В этот день повторения нет")
            return {"ok": True, "deleted": "occurrence"}
    if not c().service.delete(user, item_id):
        raise HTTPException(404, "Запись не найдена")
    return {"ok": True, "deleted": "item"}


@app.get("/api/reminders/due", response_model=list[ItemOut])
def due_reminders(minutes: int = 5, x_user_id: str | None = Header(default=None)):
    """Что наступило за последние N минут — страница показывает и озвучивает это как напоминание."""
    now = c().service.now()
    since = now - timedelta(minutes=max(1, min(minutes, 60)))
    return [item_out(i) for i in c().service.due_reminders(_user(x_user_id), since, now + timedelta(seconds=1))]


def _calendar(user: str, download: bool) -> Response:
    s = c().settings
    items = [it for it in c().repo.list_all(user) if it.due_at is not None]
    body = build_calendar(items, s.timezone, s.tz, c().service.now())
    disposition = "attachment" if download else "inline"
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'{disposition}; filename="planner.ics"'})


@app.get("/api/calendar.ics")
def calendar_for_header_user(download: bool = True, x_user_id: str | None = Header(default=None)):
    return _calendar(_user(x_user_id), download)


@app.get("/calendar/{user_id}.ics")
def calendar_subscription(user_id: str = Path(pattern=r"^[\w.@-]{1,64}$"), download: bool = False):
    """Ссылка для подписки из Google / Яндекс / Apple Календаря (календарь не умеет слать заголовки)."""
    return _calendar(user_id, download)


@app.post("/api/stt")
async def stt(file: UploadFile = File(...)):
    if c().stt is None:
        raise HTTPException(501, "Серверное распознавание не настроено (STT_BASE_URL, STT_MODEL)")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Слишком длинная запись")
    try:
        text = c().stt.transcribe(file.filename or "audio.webm", data, file.content_type)
    except Exception as exc:
        logging.getLogger(__name__).warning("STT error: %s", exc)
        raise HTTPException(502, "Сервис распознавания речи недоступен") from exc
    return {"text": text}


@app.get("/api/status")
def status():
    s = c().settings
    return {
        "nlu": c().nlu.mode,
        "llm_model": s.active_llm_model,
        "stt_server": s.stt_enabled,
        "semantic_embeddings": s.embeddings_enabled,
        "now": c().service.now().isoformat(),
        "timezone": s.timezone,
        "items": c().repo.count(),
        "calendar_path": f"/calendar/{s.default_user}.ics",
    }


@app.get("/health")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


# Браузеры и iOS запрашивают иконки по стандартным адресам в корне сайта, даже без ссылок в HTML
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC_DIR / "icons" / "favicon.ico", media_type="image/x-icon")


@app.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon():
    return FileResponse(STATIC_DIR / "icons" / "apple-touch-icon.png", media_type="image/png")
