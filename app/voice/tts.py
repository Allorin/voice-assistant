"""Озвучка на сервере через Piper — для браузеров без русского голоса (Chrome и Firefox на Linux и др.).

Обычно текст озвучивает сам браузер (speechSynthesis). Но в Linux Chrome по умолчанию не подключает системные
голоса, а у Firefox часто нет русского. Тогда страница запрашивает /api/tts и проигрывает готовый WAV.

Голос (ru_RU-irina-medium, ~63 МБ) скачивается отдельно: scripts/download_piper_voice.py.
Сервер загружает его в фоне при старте (preload). Повторяющиеся фразы берутся из кэша.

Чтобы ответ зазвучал быстрее, он озвучивается частями (split_speech): короткая первая фраза готова за доли
секунды, остальные синтезируются, пока она играет. Сервер начинает синтез сразу, как только готов ответ
на /api/chat (prefetch), — к моменту запроса страницы первая часть обычно уже в кэше.
"""
from __future__ import annotations

import importlib.util
import io
import logging
import re
import threading
import wave
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger(__name__)

MAX_CHARS = 600  # ответы ассистента короче; защищает слабый сервер
MIN_RATE, MAX_RATE = 0.7, 1.4  # те же границы, что у ползунка «Скорость» на странице
PART_CHARS = 90  # части после первой собираются до такой длины: меньше запросов и пауз между ними


def piper_available(model_path: str) -> bool:
    path = Path(model_path) if model_path else None
    return bool(path and path.is_file() and Path(f"{path}.json").is_file()
                and importlib.util.find_spec("piper") is not None)


def speakable(text: str) -> str:
    """Текст для синтеза: без ёлочек и маркеров списка; «в 15:00–16:00» → «с 15 до 16», как говорят вслух."""
    text = re.sub(r"[«»•*]", "", text)
    text = re.sub(r"\b0(\d):(\d{2})\b", r"\1:\2", text)  # «07:30» → «7:30»
    text = re.sub(r"(?:\b[вс]\s+)?(\d{1,2}:\d{2})\s*[–—-]\s*(\d{1,2}:\d{2})", r"с \1 до \2", text)
    text = re.sub(r"\b(\d{1,2}):00\b", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


def split_speech(text: str) -> list[str]:
    """Части для озвучки: первая — первая фраза (до точки, двоеточия или точки с запятой), чтобы звук начался
    быстро; дальше фразы собираются в части по ~PART_CHARS символов."""
    clauses = [c for c in re.split(r"(?<=[.!?…:;])\s+", re.sub(r"\s+", " ", text).strip()) if c]
    if not clauses:
        return []
    parts, rest = [clauses[0]], ""
    for clause in clauses[1:]:
        rest = f"{rest} {clause}" if rest else clause
        if len(rest) >= PART_CHARS:
            parts.append(rest)
            rest = ""
    if rest:
        parts.append(rest)
    return parts


def _load_voice(model_path: str):
    """Голос Piper с экономным onnxruntime: один поток и без запасающей арены памяти.
    Рядом с Vosk на сервере с 1 ГБ это на ~80 МБ меньше постоянного расхода; скорость почти та же."""
    from piper import PiperVoice

    try:
        import json

        import onnxruntime
        from piper.config import PiperConfig
        from piper.voice import ESPEAK_DATA_DIR
    except ImportError:  # другая версия piper — обычная загрузка
        return PiperVoice.load(model_path)
    opts = onnxruntime.SessionOptions()
    opts.enable_cpu_mem_arena = False
    opts.enable_mem_pattern = False
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    with open(f"{model_path}.json", encoding="utf-8") as f:
        config = PiperConfig.from_dict(json.load(f))
    session = onnxruntime.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
    return PiperVoice(config=config, session=session, espeak_data_dir=Path(ESPEAK_DATA_DIR), download_dir=Path.cwd())


class PiperTextToSpeech:
    engine = "piper"

    def __init__(self, model_path: str, cache_size: int = 64):
        self.model_path = model_path
        self._voice = None
        self._load_lock = threading.Lock()
        self._synth_lock = threading.Lock()  # один синтез за раз: на слабом сервере параллельные только мешают
        self._cache: OrderedDict[tuple[str, float], bytes] = OrderedDict()
        self._cache_size = cache_size
        self._state_lock = threading.Lock()
        self._inflight: dict[tuple[str, float], Future] = {}  # одну и ту же часть не синтезируем дважды
        self._background = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-prefetch")

    def _get_voice(self):
        if self._voice is None:
            with self._load_lock:
                if self._voice is None:
                    log.info("Загружаю голос для озвучки %s", self.model_path)
                    self._voice = _load_voice(self.model_path)
        return self._voice

    def preload(self) -> None:
        try:
            self._get_voice()
            self.synthesize("Готово.")  # первый синтез после загрузки заметно медленнее — делаем его заранее
        except Exception:  # без голоса сервера озвучивает только браузер
            log.exception("Не удалось загрузить голос Piper %s", self.model_path)

    def prefetch(self, parts: list[str], rate: float) -> None:
        """Озвучить части ответа в фоне по порядку, пока страница получает ответ и начинает играть."""
        def run() -> None:
            for part in parts:
                try:
                    self.synthesize(part, rate)
                except Exception:  # ошибку увидит сама страница, когда запросит эту часть
                    return
        self._background.submit(run)

    def synthesize(self, text: str, rate: float = 1.0) -> bytes:
        """WAV (моно, 16 бит) с озвученным текстом. rate — скорость речи, как у speechSynthesis."""
        text = speakable(text)
        if not text:
            return b""
        rate = round(min(MAX_RATE, max(MIN_RATE, rate)), 2)
        key = (text, rate)
        with self._state_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = self._inflight[key] = Future()
        if not owner:  # эту часть уже синтезирует фоновая задача или другой запрос — ждём её результат
            return future.result(timeout=60)
        try:
            with self._synth_lock:
                data = self._render(text, rate)
            with self._state_lock:
                self._cache[key] = data
                if len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            future.set_result(data)
            return data
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._state_lock:
                self._inflight.pop(key, None)

    def _render(self, text: str, rate: float) -> bytes:
        from piper import SynthesisConfig

        voice = self._get_voice()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=voice.config.length_scale / rate))
        return buf.getvalue()
