"""HTTP API (FastAPI) и раздача веб-интерфейса."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import threading
import webbrowser
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from urllib.parse import quote, unquote

from fastapi import (
    FastAPI,
    File,
    Header,
    HTTPException,
    Path,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.access import calendar_token
from app.config import BASE_DIR, get_settings
from app.container import USER_ID_RX, Container, build
from app.dialog import phrases as ph
from app.models import Item
from app.services.ics import build_calendar
from app.voice.tts import split_speech
from app.voice.vosk_stt import AudioFormatError, BusyError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC_DIR = BASE_DIR / "app" / "static"

container: Container | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global container
    container = build(get_settings())
    # модели Vosk и голос Piper грузятся в фоне: сервер стартует сразу
    if getattr(container.stt, "streaming", False):
        threading.Thread(target=container.stt.preload, daemon=True).start()
    if container.tts is not None:
        threading.Thread(target=container.tts.preload, daemon=True).start()
    url = os.getenv("OPEN_BROWSER_URL")
    if url:  # start.bat: открыть страницу, только когда сервер точно поднялся
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    try:
        yield
    finally:
        if container.nlu.llm is not None:
            container.nlu.llm.close()


app = FastAPI(title="Голосовой ежедневник", lifespan=lifespan)


@app.middleware("http")
async def access_code(request: Request, call_next):
    """Стенд с ACCESS_CODE: запросы к /api только с верным кодом (страница спросит его один раз)."""
    if request.url.path.startswith("/api/") and container is not None and container.access.enabled:
        supplied = unquote(request.headers.get("x-access-code", "")).strip()
        client = request.client.host if request.client else "?"
        status = container.access.check(client, supplied)
        if status == 429:
            return JSONResponse({"detail": "Слишком много неверных попыток. Подождите минуту."}, status_code=429)
        if status == 401:
            detail = "Неверный код доступа" if supplied else "Нужен код доступа"
            return JSONResponse({"detail": detail, "access_code_required": True}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def fresh_static(request: Request, call_next):
    """Страница и скрипты — без долгого кэша: после обновления стенда браузер сверяет файлы (ETag) и берёт новые,
    иначе старый app.js из кэша работает с новым сервером."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def c() -> Container:
    assert container is not None
    return container


def _user(x_user_id: str | None) -> str:
    """Профиль из заголовка X-User-Id. Браузер кодирует кириллицу (encodeURIComponent): раскодируем."""
    raw = x_user_id if isinstance(x_user_id, str) else ""  # при прямом вызове функции тут объект Header
    user = unquote(raw.strip()).strip()
    if not user:
        return c().settings.default_user
    if not USER_ID_RX.match(user):
        raise HTTPException(400, "Имя профиля: до 64 букв, цифр и знаков . @ - _")
    c().prepare_user(user)
    return user


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
    # страница озвучит ответ голосом сервера с этой скоростью — сервер начнёт синтез сразу
    tts_rate: float | None = Field(default=None, ge=0.5, le=2)


class ChatOut(BaseModel):
    reply: str
    speech: str
    intent: str
    awaiting: str | None
    nlu: str
    items: list[ItemOut]
    changed: list[int]
    speech_parts: list[str] = []  # части speech для озвучки на сервере (если голос сервера подключён)


@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn, x_user_id: str | None = Header(default=None)):
    r = c().dialog.handle(body.session_id, body.text, _user(x_user_id), collect_details=body.collect_details)
    speech = r.speech or r.text
    parts = split_speech(speech) if c().tts is not None else []
    if parts and body.tts_rate:
        c().tts.prefetch(parts, body.tts_rate)  # пока ответ идёт до страницы, первая часть уже озвучивается
    return ChatOut(reply=r.text, speech=speech, intent=r.intent, awaiting=r.awaiting, nlu=r.nlu,
                   items=[item_out(i) for i in r.items[:30]], changed=r.changed, speech_parts=parts)


@app.post("/api/session/reset")
def reset_session(body: ChatIn, x_user_id: str | None = Header(default=None)):
    c().dialog.sessions.reset(body.session_id, _user(x_user_id))
    return {"ok": True}


@app.get("/api/items/upcoming", response_model=list[ItemOut])
def upcoming(days: int = 7, x_user_id: str | None = Header(default=None)):
    return [item_out(i) for i in c().service.upcoming(_user(x_user_id), max(1, min(days, 60)))]


@app.get("/api/items/undated", response_model=list[ItemOut])
def undated(x_user_id: str | None = Header(default=None)):
    """Задачи без срока: в плане они идут отдельным блоком под ближайшими днями."""
    return [item_out(i) for i in c().service.undated(_user(x_user_id))]


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
def calendar_subscription(user_id: str = Path(pattern=r"^[\w.@-]{1,64}$"), download: bool = False,
                          token: str = ""):
    """Ссылка для подписки из Google / Яндекс / Apple Календаря (календарь не умеет слать заголовки).
    На стенде с ключом ссылка содержит секретный token, без него — 404, как будто календаря нет."""
    expected = calendar_token(c().settings.calendar_secret, user_id)
    if expected and not hmac.compare_digest(token, expected):
        raise HTTPException(404, "Календарь не найден")
    return _calendar(user_id, download)


def calendar_path(user: str) -> str:
    token = calendar_token(c().settings.calendar_secret, user)
    return f"/calendar/{quote(user)}.ics" + (f"?token={token}" if token else "")


@app.post("/api/stt")
async def stt(file: UploadFile = File(...)):
    if c().stt is None:
        raise HTTPException(501, "Серверное распознавание не настроено (модель Vosk или STT_BASE_URL, STT_MODEL)")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Слишком длинная запись")
    try:
        # распознавание долгое и нагружает процессор — в отдельном потоке, чтобы не держать остальные запросы
        text = await run_in_threadpool(c().stt.transcribe, file.filename or "audio.wav", data, file.content_type)
    except AudioFormatError as exc:
        # webm присылает только прежняя версия страницы, оставшаяся в кэше браузера
        old_page = (file.filename or "").endswith(".webm") or (file.content_type or "").startswith("audio/webm")
        detail = "страница устарела, обновите её с очисткой кэша (Ctrl+F5)" if old_page else str(exc)
        raise HTTPException(400, detail) from exc
    except Exception as exc:
        logging.getLogger(__name__).warning("STT error: %s", exc)
        raise HTTPException(502, "Сервис распознавания речи недоступен") from exc
    return {"text": text}


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    rate: float = 1.0


@app.post("/api/tts")
async def tts(req: TTSRequest):
    """Озвучка на сервере для браузеров без русского голоса: WAV с текстом."""
    if c().tts is None:
        raise HTTPException(501, "Озвучка на сервере не настроена (голос Piper)")
    try:
        data = await run_in_threadpool(c().tts.synthesize, req.text, req.rate)
    except Exception as exc:
        logging.getLogger(__name__).warning("TTS error: %s", exc)
        raise HTTPException(502, "Озвучка на сервере недоступна") from exc
    return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.websocket("/api/stt/stream")
async def stt_stream(ws: WebSocket):
    """Потоковое распознавание: {"rate", "code"} → куски PCM 16 бит → "end".
    Ответы: {"partial": текст} по ходу речи, {"text": итог} в конце, {"error", "status"} при отказе.
    Код доступа приходит первым сообщением: заголовки WebSocket браузер задать не даёт, а в адресе он попал бы в логи."""
    await ws.accept()

    async def refuse(status: int, detail: str) -> None:
        try:
            await ws.send_json({"error": detail, "status": status})
            await ws.close()
        except (WebSocketDisconnect, RuntimeError):  # клиент уже ушёл
            pass

    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        rate = int(hello.get("rate", 16000))
    except (asyncio.TimeoutError, ValueError, TypeError, AttributeError, KeyError, WebSocketDisconnect):
        return await refuse(400, "Ожидалось {\"rate\": 16000}")
    client = ws.client.host if ws.client else "?"
    access = c().access.check(client, str(hello.get("code") or "").strip())
    if access != 200:
        return await refuse(access, "Нужен код доступа" if access == 401 else "Слишком много неверных попыток")
    if not getattr(c().stt, "streaming", False):
        return await refuse(501, "Потоковое распознавание не настроено")
    if not 8000 <= rate <= 48000:
        return await refuse(400, "Частота звука должна быть от 8 до 48 кГц")
    try:
        stream = await run_in_threadpool(c().stt.open_stream, rate)
    except BusyError as exc:
        return await refuse(503, str(exc))
    except Exception as exc:
        logging.getLogger(__name__).warning("STT stream error: %s", exc)
        return await refuse(502, "Распознавание речи недоступно")
    try:
        last = ""
        while True:
            message = await asyncio.wait_for(ws.receive(), timeout=15)
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes"):
                text = await run_in_threadpool(stream.feed, message["bytes"])
                if text != last:
                    last = text
                    await ws.send_json({"partial": text})
            elif message.get("text") is not None:  # "end": человек закончил говорить
                await ws.send_json({"text": await run_in_threadpool(stream.finish)})
                await ws.close()
                return
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        return
    finally:
        stream.close()


@app.get("/api/status")
def status(x_user_id: str | None = Header(default=None)):
    s = c().settings
    user = _user(x_user_id)
    return {
        "nlu": c().nlu.mode,
        "llm_model": s.active_llm_model if c().nlu.llm is not None else None,
        "stt_server": c().stt is not None,
        "stt_stream": getattr(c().stt, "streaming", False),
        "tts_server": c().tts is not None,
        "semantic_embeddings": s.embeddings_enabled,
        "now": c().service.now().isoformat(),
        "timezone": s.timezone,
        "items": c().repo.count(user),
        "user": user,
        "default_user": s.default_user,
        "public_demo": s.public_demo,
        "calendar_path": calendar_path(user),
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
