"""Серверное распознавание Vosk: настоящая модель не нужна — модуль vosk подменяется."""
import importlib.machinery
import io
import json
import sys
import types
import wave
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.clock import FixedClock
from app.container import build, build_stt
from app.voice.stt import SpeechToText
from app.voice.vosk_stt import (
    MAX_SECONDS,
    AudioFormatError,
    BusyError,
    VoskSpeechToText,
    vosk_available,
)
from tests.conftest import NOW, make_settings


def wav_bytes(seconds: float = 1.0, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate) * channels * (width // 2 or 1))
    return buf.getvalue()


@pytest.fixture
def fake_vosk(monkeypatch):
    calls = {"models": [], "rates": [], "bytes": 0}

    class Model:
        def __init__(self, path):
            calls["models"].append(path)

    class KaldiRecognizer:
        """Для потоковых тестов «звук» — это слова: b"W:слово" добавляет слово, b"PAUSE!" завершает отрезок."""

        def __init__(self, model, rate):
            calls["rates"].append(rate)
            self.words = []

        def AcceptWaveform(self, data):
            calls["bytes"] += len(data)
            if data == b"PAUSE!":
                return True
            if data.startswith(b"W:"):
                self.words.append(data[2:].decode().strip())
            return False

        def _take(self):
            text, self.words = " ".join(self.words), []
            return text

        def PartialResult(self):
            return json.dumps({"partial": " ".join(self.words)}, ensure_ascii=False)

        def Result(self):
            return json.dumps({"text": self._take()}, ensure_ascii=False)

        def FinalResult(self):
            text = self._take() if self.words else " напомни завтра купить хлеб "
            return json.dumps({"text": text}, ensure_ascii=False)

    module = types.ModuleType("vosk")
    module.__spec__ = importlib.machinery.ModuleSpec("vosk", None)
    module.Model, module.KaldiRecognizer, module.SetLogLevel = Model, KaldiRecognizer, lambda level: None
    monkeypatch.setitem(sys.modules, "vosk", module)
    return calls


@pytest.fixture
def model_dir(tmp_path):
    (tmp_path / "am").mkdir()
    return str(tmp_path)


def test_transcribes_wav_and_loads_model_once(fake_vosk, model_dir):
    stt = VoskSpeechToText(model_dir)
    assert fake_vosk["models"] == []  # модель грузится только при первом запросе
    assert stt.transcribe("speech.wav", wav_bytes(1.0), "audio/wav") == "напомни завтра купить хлеб"
    assert stt.transcribe("speech.wav", wav_bytes(0.5, rate=48000), "audio/wav") == "напомни завтра купить хлеб"
    assert fake_vosk["models"] == [model_dir]
    assert fake_vosk["rates"] == [16000, 48000]
    assert fake_vosk["bytes"] == 16000 * 2 + 24000 * 2


def test_long_recording_is_cut(fake_vosk, model_dir):
    VoskSpeechToText(model_dir).transcribe("speech.wav", wav_bytes(MAX_SECONDS + 5, rate=8000))
    assert fake_vosk["bytes"] == MAX_SECONDS * 8000 * 2


@pytest.mark.parametrize("data", [b"not a wav", b"", wav_bytes(channels=2), wav_bytes(width=1)])
def test_rejects_other_formats(fake_vosk, model_dir, data):
    with pytest.raises(AudioFormatError):
        VoskSpeechToText(model_dir).transcribe("speech.webm", data, "audio/webm")


def test_empty_wav_gives_empty_text(fake_vosk, model_dir):
    assert VoskSpeechToText(model_dir).transcribe("speech.wav", wav_bytes(0)) == ""
    assert fake_vosk["models"] == []


def test_vosk_available_needs_model_and_package(fake_vosk, model_dir, tmp_path, monkeypatch):
    assert vosk_available(model_dir)
    assert not vosk_available("")
    assert not vosk_available(str(tmp_path / "missing"))
    monkeypatch.setitem(sys.modules, "vosk", None)  # пакет не установлен
    assert not vosk_available(model_dir)


def test_container_picks_engine(fake_vosk, model_dir):
    assert build_stt(make_settings(seed=False)) is None
    assert isinstance(build_stt(replace(make_settings(seed=False), stt_vosk_model=model_dir)), VoskSpeechToText)
    api = replace(make_settings(seed=False), stt_vosk_model=model_dir, stt_base_url="http://localhost:9/v1",
                  stt_model="whisper")
    assert isinstance(build_stt(api), SpeechToText)  # внешний сервис, если настроен, важнее


def test_api_with_vosk(fake_vosk, model_dir):
    main.container = build(replace(make_settings(seed=False), stt_vosk_model=model_dir), FixedClock(NOW))
    c = TestClient(main.app)
    assert c.get("/api/status").json()["stt_server"] is True
    r = c.post("/api/stt", files={"file": ("speech.wav", wav_bytes(), "audio/wav")})
    assert r.status_code == 200 and r.json() == {"text": "напомни завтра купить хлеб"}
    r = c.post("/api/stt", files={"file": ("speech.webm", b"webm", "audio/webm")})
    assert r.status_code == 400 and "WAV" in r.json()["detail"]


def test_status_without_server_stt():
    main.container = build(make_settings(seed=False), FixedClock(NOW))
    assert TestClient(main.app).get("/api/status").json()["stt_server"] is False


def word(text: str) -> bytes:
    data = f"W:{text}".encode()
    return data + b" " * (len(data) % 2)  # звук приходит целыми 16-битными отсчётами


def test_stream_gives_partial_text_and_final(fake_vosk, model_dir):
    stt = VoskSpeechToText(model_dir, max_parallel=1)
    stream = stt.open_stream(16000)
    assert stream.feed(word("напомни")) == "напомни"
    assert stream.feed(word("завтра")) == "напомни завтра"
    assert stream.feed(b"PAUSE!") == "напомни завтра"  # отрезок закончен, текст не пропадает
    assert stream.feed(b"\x01") == "напомни завтра"  # обрывок отсчёта не отправляется в модель
    assert stream.feed(word("хлеб")) == "напомни завтра хлеб"
    assert stream.finish() == "напомни завтра хлеб"
    with pytest.raises(BusyError):  # единственный слот занят, пока поток не закрыт
        stt.open_stream(16000, wait=0.01)
    stream.close()
    stream.close()  # повторное закрытие не освобождает слот дважды
    stt.open_stream(16000, wait=0.01).close()


def test_stream_is_limited_in_length(fake_vosk, model_dir):
    stream = VoskSpeechToText(model_dir).open_stream(8000)
    for _ in range(MAX_SECONDS * 2):
        stream.feed(b"\x01\x00" * 8000)
    assert fake_vosk["bytes"] == MAX_SECONDS * 8000 * 2


def ws_client(settings) -> TestClient:
    main.container = build(settings, FixedClock(NOW))
    return TestClient(main.app)


def test_websocket_stream(fake_vosk, model_dir):
    c = ws_client(replace(make_settings(seed=False), stt_vosk_model=model_dir))
    assert c.get("/api/status").json()["stt_stream"] is True
    with c.websocket_connect("/api/stt/stream") as ws:
        ws.send_json({"rate": 16000})
        ws.send_bytes(word("добавь"))
        assert ws.receive_json() == {"partial": "добавь"}
        ws.send_bytes(word("встречу"))
        assert ws.receive_json() == {"partial": "добавь встречу"}
        ws.send_bytes(b"\x01")  # текст не изменился — ответа нет
        ws.send_text("end")
        assert ws.receive_json() == {"text": "добавь встречу"}


def test_websocket_checks_access_code(fake_vosk, model_dir):
    c = ws_client(replace(make_settings(seed=False), stt_vosk_model=model_dir, access_code="секрет"))
    with c.websocket_connect("/api/stt/stream") as ws:
        ws.send_json({"rate": 16000, "code": "не тот"})
        assert ws.receive_json()["status"] == 401
    with c.websocket_connect("/api/stt/stream") as ws:
        ws.send_json({"rate": 16000, "code": "секрет"})
        ws.send_text("end")
        assert ws.receive_json() == {"text": "напомни завтра купить хлеб"}


@pytest.mark.parametrize(("hello", "status"), [({"rate": 16000}, 501), ("не json", 400)])
def test_websocket_refuses(hello, status):
    c = ws_client(make_settings(seed=False))  # без модели Vosk
    assert c.get("/api/status").json()["stt_stream"] is False
    with c.websocket_connect("/api/stt/stream") as ws:
        ws.send_text(json.dumps(hello) if isinstance(hello, dict) else hello)
        assert ws.receive_json()["status"] == status


def test_websocket_rejects_bad_rate(fake_vosk, model_dir):
    c = ws_client(replace(make_settings(seed=False), stt_vosk_model=model_dir))
    with c.websocket_connect("/api/stt/stream") as ws:
        ws.send_json({"rate": 1000})
        assert ws.receive_json()["status"] == 400


def _zip(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "x")


def test_download_script(tmp_path, monkeypatch):
    sys.path.insert(0, str(main.BASE_DIR / "scripts"))
    try:
        import download_vosk_model as dl
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(dl, "MODELS_DIR", tmp_path / "models")
    names = [f"{dl.NAME}/am/final.mdl", f"{dl.NAME}/conf/model.conf"]
    monkeypatch.setattr(dl.urllib.request, "urlretrieve", lambda url, path, hook=None: _zip(path, names))
    assert dl.main() == 0
    assert (tmp_path / "models" / dl.NAME / "am" / "final.mdl").exists()
    assert dl.main() == 0  # уже скачана

    bad = tmp_path / "bad"
    monkeypatch.setattr(dl, "MODELS_DIR", bad)
    monkeypatch.setattr(dl.urllib.request, "urlretrieve", lambda url, path, hook=None: _zip(path, ["../evil.txt"]))
    assert dl.main() == 1
    assert not (tmp_path / "evil.txt").exists() and not (bad / dl.NAME).exists()
