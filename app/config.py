"""Настройки приложения. Всё читается из переменных окружения (или файла .env)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


CERT_DIRS = (BASE_DIR / ".certs", BASE_DIR / "certs")


def resolve_ca_bundle(value: str) -> str:
    """Сертификат НУЦ Минцифры для GigaChat.

    Путь из .env может быть относительным (от папки проекта). Если файла нет, например в .env
    остался путь с чужого компьютера, берётся первый сертификат из .certs/ или certs/.
    Ничего не нашлось: пустая строка, используются системные сертификаты.
    """
    if value:
        path = Path(value)
        path = path if path.is_absolute() else BASE_DIR / path
        if path.is_file():
            return str(path)
        logging.getLogger(__name__).warning(
            "GIGACHAT_CA_BUNDLE_FILE: файл %s не найден, ищу сертификат в папке .certs", value)
    for folder in CERT_DIRS:
        for pattern in ("*.pem", "*.crt", "*.cer"):
            found = sorted(folder.glob(pattern))
            if found:
                return str(found[0])
    return ""


@dataclass(frozen=True)
class Settings:
    database_url: str
    timezone: str
    seed_csv: str
    default_user: str

    # LLM: любой провайдер с OpenAI-совместимым API (Groq, OpenRouter, Gemini, Ollama, ...)
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float

    # Серверное распознавание речи (опционально, OpenAI-совместимый /audio/transcriptions)
    stt_base_url: str
    stt_api_key: str
    stt_model: str

    # Эмбеддинги для семантического поиска (опционально, OpenAI-совместимый /embeddings)
    embeddings_base_url: str
    embeddings_api_key: str
    embeddings_model: str

    llm_provider: str = "openai"
    gigachat_credentials: str = field(default="", repr=False)
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-2-Max"
    gigachat_ca_bundle_file: str = ""

    # Офлайн-распознавание речи Vosk: папка с моделью (scripts/download_vosk_model.py). Пусто — выключено
    stt_vosk_model: str = ""
    # Озвучка на сервере Piper (для браузеров без русского голоса): файл голоса .onnx. Пусто — выключена
    tts_piper_voice: str = ""

    # Публичный стенд: у каждого посетителя свой гостевой профиль с копией демо-записей
    public_demo: bool = False

    # Общий код доступа к стенду: без него /api отвечает 401 (app/access.py). Пусто — доступ открыт
    access_code: str = field(default="", repr=False)
    # Ключ для секретных ссылок подписки на календарь; если не задан — используется ACCESS_CODE
    calendar_key: str = field(default="", repr=False)

    def __post_init__(self):
        if self.llm_provider not in ("openai", "gigachat"):
            raise ValueError("LLM_PROVIDER должен быть openai или gigachat")

    @property
    def tz(self) -> tzinfo:
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            # на Windows база поясов берётся из пакета tzdata; если его нет — системный пояс компьютера
            logging.getLogger(__name__).warning(
                "Часовой пояс %s не найден (pip install tzdata), используется системный", self.timezone)
            return datetime.now().astimezone().tzinfo or timezone.utc

    @property
    def calendar_secret(self) -> str:
        return self.calendar_key or self.access_code

    @property
    def llm_enabled(self) -> bool:
        if self.llm_provider == "gigachat":
            return bool(self.gigachat_credentials and self.gigachat_model)
        return bool(self.llm_base_url and self.llm_model)

    @property
    def active_llm_model(self) -> str | None:
        if not self.llm_enabled:
            return None
        return self.gigachat_model if self.llm_provider == "gigachat" else self.llm_model

    @property
    def stt_enabled(self) -> bool:
        return bool(self.stt_base_url and self.stt_model)

    @property
    def embeddings_enabled(self) -> bool:
        return bool(self.embeddings_base_url and self.embeddings_model)


@lru_cache
def get_settings() -> Settings:
    # На Amvera (переменная AMVERA=1) постоянный диск смонтирован в /data, стенд публичный
    on_amvera = _env("AMVERA") == "1"
    default_db = "sqlite:////data/assistant.db" if on_amvera else f"sqlite:///{BASE_DIR / 'data' / 'assistant.db'}"
    gigachat_key = _env("GIGACHAT_CREDENTIALS").strip("'\"")  # ключ, вставленный в панель хостинга в кавычках
    return Settings(
        database_url=_env("DATABASE_URL", default_db),
        timezone=_env("TIMEZONE", "Europe/Moscow"),
        seed_csv=_env("SEED_CSV", str(BASE_DIR / "data" / "events_seed.csv")),
        default_user=_env("DEFAULT_USER", "demo"),
        llm_base_url=_env("LLM_BASE_URL"),
        llm_api_key=_env("LLM_API_KEY", "none"),
        llm_model=_env("LLM_MODEL"),
        llm_timeout=float(_env("LLM_TIMEOUT", "20")),
        stt_base_url=_env("STT_BASE_URL"),
        stt_api_key=_env("STT_API_KEY", "none"),
        stt_model=_env("STT_MODEL"),
        stt_vosk_model=_env("STT_VOSK_MODEL", str(BASE_DIR / "models" / "vosk-model-small-ru-0.22")),
        tts_piper_voice=_env("TTS_PIPER_VOICE", str(BASE_DIR / "models" / "piper" / "ru_RU-irina-medium.onnx")),
        embeddings_base_url=_env("EMBEDDINGS_BASE_URL"),
        embeddings_api_key=_env("EMBEDDINGS_API_KEY", "none"),
        embeddings_model=_env("EMBEDDINGS_MODEL"),
        # задан только ключ GigaChat — значит, работаем через GigaChat
        llm_provider=_env("LLM_PROVIDER", "gigachat" if gigachat_key else "openai").lower(),
        gigachat_credentials=gigachat_key,
        gigachat_scope=_env("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        gigachat_model=_env("GIGACHAT_MODEL", "GigaChat-2-Max"),
        gigachat_ca_bundle_file=resolve_ca_bundle(_env("GIGACHAT_CA_BUNDLE_FILE")),
        public_demo=_env("PUBLIC_DEMO", "1" if on_amvera else "").lower() in ("1", "true", "yes", "on"),
        access_code=_env("ACCESS_CODE").strip("'\""),
        calendar_key=_env("CALENDAR_KEY").strip("'\""),
    )
