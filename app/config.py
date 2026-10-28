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

    # Публичный стенд: у каждого посетителя свой гостевой профиль с копией демо-записей
    public_demo: bool = False

    # Резервная копия SQLite в приватном датасете Hugging Face (временный диск бесплатного Space)
    backup_repo: str = ""
    hf_token: str = field(default="", repr=False)

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
    default_db = f"sqlite:///{BASE_DIR / 'data' / 'assistant.db'}"
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
        embeddings_base_url=_env("EMBEDDINGS_BASE_URL"),
        embeddings_api_key=_env("EMBEDDINGS_API_KEY", "none"),
        embeddings_model=_env("EMBEDDINGS_MODEL"),
        llm_provider=_env("LLM_PROVIDER", "openai").lower(),
        gigachat_credentials=_env("GIGACHAT_CREDENTIALS"),
        gigachat_scope=_env("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        gigachat_model=_env("GIGACHAT_MODEL", "GigaChat-2-Max"),
        gigachat_ca_bundle_file=resolve_ca_bundle(_env("GIGACHAT_CA_BUNDLE_FILE")),
        public_demo=_env("PUBLIC_DEMO").lower() in ("1", "true", "yes", "on"),
        backup_repo=_env("BACKUP_REPO"),
        hf_token=_env("HF_TOKEN"),
    )
