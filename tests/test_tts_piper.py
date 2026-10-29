"""Озвучка на сервере Piper: настоящий голос не нужен — модуль piper подменяется."""
import importlib.machinery
import io
import sys
import types
import wave
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.clock import FixedClock
from app.container import build
from app.voice.tts import PiperTextToSpeech, piper_available, speakable
from tests.conftest import NOW, make_settings


@pytest.fixture
def fake_piper(monkeypatch):
    calls = {"loads": [], "texts": [], "scales": []}

    class SynthesisConfig:
        def __init__(self, length_scale=None, **_):
            self.length_scale = length_scale

    class PiperVoice:
        config = types.SimpleNamespace(length_scale=1.2)

        @classmethod
        def load(cls, path):
            calls["loads"].append(path)
            return cls()

        def synthesize_wav(self, text, wav_file, syn_config=None):
            calls["texts"].append(text)
            calls["scales"].append(syn_config.length_scale)
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x01" * 2205)

    module = types.ModuleType("piper")
    module.__spec__ = importlib.machinery.ModuleSpec("piper", None)
    module.PiperVoice, module.SynthesisConfig = PiperVoice, SynthesisConfig
    monkeypatch.setitem(sys.modules, "piper", module)
    return calls


@pytest.fixture
def voice_file(tmp_path):
    model = tmp_path / "ru_RU-irina-medium.onnx"
    model.write_bytes(b"onnx")
    (tmp_path / "ru_RU-irina-medium.onnx.json").write_text("{}")
    return str(model)


def test_speakable_reads_times_like_people_do():
    assert speakable("Встреча добавлена: «Созвон», завтра в 15:00–16:00.") == \
        "Встреча добавлена: Созвон, завтра с 15 до 16."
    assert speakable("• Напомню в 07:30 и в 9:00") == "Напомню в 7:30 и в 9"
    assert len(speakable("а" * 5000)) == 600


def test_piper_available_needs_model_config_and_package(fake_piper, voice_file, tmp_path, monkeypatch):
    assert piper_available(voice_file)
    assert not piper_available("")
    assert not piper_available(str(tmp_path / "missing.onnx"))
    (tmp_path / "ru_RU-irina-medium.onnx.json").unlink()
    assert not piper_available(voice_file)  # без файла настроек голос не загрузится


def test_synthesize_returns_wav_and_caches(fake_piper, voice_file):
    tts = PiperTextToSpeech(voice_file, cache_size=2)
    assert fake_piper["loads"] == []  # голос грузится при первом запросе (или preload)
    data = tts.synthesize("Встреча в 15:00", rate=1.0)
    with wave.open(io.BytesIO(data)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 22050)
    assert fake_piper["texts"] == ["Встреча в 15"]
    assert tts.synthesize("Встреча в 15:00", rate=1.0) == data  # из кэша
    assert len(fake_piper["texts"]) == 1
    tts.synthesize("Вторая", rate=1.0)
    tts.synthesize("Третья", rate=1.0)
    tts.synthesize("Встреча в 15:00", rate=1.0)  # вытеснена из кэша на две фразы
    assert len(fake_piper["texts"]) == 4
    assert fake_piper["loads"] == [voice_file]
    assert tts.synthesize("  «»  ") == b""


def test_rate_changes_speed_within_slider_limits(fake_piper, voice_file):
    tts = PiperTextToSpeech(voice_file)
    tts.synthesize("Быстро", rate=1.2)
    tts.synthesize("Слишком быстро", rate=5)
    tts.synthesize("Слишком медленно", rate=0.1)
    assert fake_piper["scales"] == pytest.approx([1.0, 1.2 / 1.4, 1.2 / 0.7])


def test_api_tts(fake_piper, voice_file):
    main.container = build(replace(make_settings(seed=False), tts_piper_voice=voice_file), FixedClock(NOW))
    c = TestClient(main.app)
    assert c.get("/api/status").json()["tts_server"] is True
    r = c.post("/api/tts", json={"text": "Привет", "rate": 1.05})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content.startswith(b"RIFF")
    assert c.post("/api/tts", json={"text": ""}).status_code == 422


def test_api_tts_not_configured():
    main.container = build(make_settings(seed=False), FixedClock(NOW))
    c = TestClient(main.app)
    assert c.get("/api/status").json()["tts_server"] is False
    assert c.post("/api/tts", json={"text": "Привет"}).status_code == 501


def test_download_voice_script(tmp_path, monkeypatch):
    sys.path.insert(0, str(main.BASE_DIR / "scripts"))
    try:
        import download_piper_voice as dl
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(dl, "MODELS_DIR", tmp_path)
    urls = []

    def fake_download(url, path, size=2_000_000):
        urls.append(url)
        path.write_bytes(b"x" * (size if path.suffix == ".onnx" else 10))

    monkeypatch.setattr(dl, "_download", fake_download)
    assert dl.main([]) == 0
    assert (tmp_path / "ru_RU-irina-medium.onnx").stat().st_size == 2_000_000
    assert urls[-1].endswith("/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx")
    assert dl.main([]) == 0 and len(urls) == 2  # уже скачан
    assert dl.main(["robot"]) == 1

    monkeypatch.setattr(dl, "_download", lambda url, path: fake_download(url, path, size=100))
    assert dl.main(["denis"]) == 1  # вместо модели пришла страница с ошибкой
    assert not list(tmp_path.glob("ru_RU-denis-*"))
