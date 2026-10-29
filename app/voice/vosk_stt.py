"""Офлайн-распознавание речи на сервере через Vosk — без внешних сервисов и ключей.

Нужно для браузеров, которые не умеют распознавать речь сами (Firefox, Opera, Яндекс Браузер и др.).
Два пути:
- потоковый (WebSocket /api/stt/stream): звук идёт кусками по ходу речи, промежуточный текст сразу
  возвращается и показывается в поле ввода, а к концу фразы результат уже готов;
- запасной (POST /api/stt): запись целиком в WAV (16 кГц, моно, 16 бит).

Модель (vosk-model-small-ru-0.22, ~45 МБ, ~180 МБ в памяти) скачивается отдельно:
scripts/download_vosk_model.py. Сервер загружает её в фоне при старте (preload).
"""
from __future__ import annotations

import importlib.util
import io
import json
import logging
import threading
import wave
from pathlib import Path

log = logging.getLogger(__name__)

MAX_SECONDS = 30  # дольше команды не бывают; защищает слабый сервер от длинных записей


class AudioFormatError(ValueError):
    """Запись не в формате WAV PCM 16 бит."""


class BusyError(RuntimeError):
    """Все слоты распознавания заняты."""


def vosk_available(model_path: str) -> bool:
    return bool(model_path) and (Path(model_path) / "am").is_dir() and importlib.util.find_spec("vosk") is not None


class VoskStream:
    """Распознавание одной фразы по кускам: feed() возвращает текст на данный момент, finish() — итог."""

    def __init__(self, recognizer, rate: int, release=lambda: None):
        self._rec = recognizer
        self._left = rate * 2 * MAX_SECONDS  # байт, которые ещё можно принять
        self._done: list[str] = []
        self._text = ""
        self._release = release

    def _join(self, *extra: str) -> str:
        return " ".join(t for t in (*self._done, *extra) if t).strip()

    def feed(self, chunk: bytes) -> str:
        chunk = chunk[: min(len(chunk) // 2 * 2, self._left)]  # целые 16-битные отсчёты, не дольше MAX_SECONDS
        if not chunk:
            return self._text
        self._left -= len(chunk)
        if self._rec.AcceptWaveform(chunk):  # Vosk закончил отрезок речи после паузы
            self._done.append(json.loads(self._rec.Result()).get("text", ""))
            self._text = self._join()
        else:
            self._text = self._join(json.loads(self._rec.PartialResult()).get("partial", ""))
        return self._text

    def finish(self) -> str:
        self._done.append(json.loads(self._rec.FinalResult()).get("text", ""))
        return self._join()

    def close(self) -> None:
        release, self._release = self._release, lambda: None
        release()


class VoskSpeechToText:
    engine = "vosk"
    streaming = True

    def __init__(self, model_path: str, max_parallel: int = 2):
        self.model_path = model_path
        self._model = None
        self._load_lock = threading.Lock()
        # распознавание занимает процессор целиком; больше двух записей сразу слабый сервер не потянет
        self._slots = threading.BoundedSemaphore(max_parallel)

    def _get_model(self):
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    import vosk

                    vosk.SetLogLevel(-1)
                    log.info("Загружаю модель распознавания речи %s", self.model_path)
                    self._model = vosk.Model(self.model_path)
        return self._model

    def preload(self) -> None:
        """Загрузить модель заранее (в фоне при старте сервера), чтобы первая фраза не ждала."""
        try:
            self._get_model()
        except Exception:  # без модели работает только распознавание в браузере
            log.exception("Не удалось загрузить модель Vosk %s", self.model_path)

    def open_stream(self, rate: int, wait: float = 10.0) -> VoskStream:
        """Начать потоковое распознавание; занимает слот до VoskStream.close()."""
        if not self._slots.acquire(timeout=wait):
            raise BusyError("Распознавание занято, попробуйте через пару секунд")
        try:
            import vosk

            return VoskStream(vosk.KaldiRecognizer(self._get_model(), rate), rate, self._slots.release)
        except Exception:
            self._slots.release()
            raise

    def transcribe(self, filename: str, data: bytes, content_type: str | None = None) -> str:
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
                    raise AudioFormatError("Нужна запись WAV: моно, 16 бит")
                rate = wav.getframerate()
                frames = wav.readframes(min(wav.getnframes(), rate * MAX_SECONDS))
        except (wave.Error, EOFError) as exc:
            raise AudioFormatError("Нужна запись в формате WAV") from exc
        if not frames:
            return ""

        import vosk

        with self._slots:
            recognizer = vosk.KaldiRecognizer(self._get_model(), rate)
            step = rate // 2 * 2  # по полсекунды; 2 байта на отсчёт
            for start in range(0, len(frames), step):
                recognizer.AcceptWaveform(frames[start:start + step])
            result = json.loads(recognizer.FinalResult() or "{}")
        return (result.get("text") or "").strip()
