"""Сборка зависимостей приложения в одном месте (простой DI без фреймворков)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.clock import Clock
from app.config import Settings
from app.dialog.manager import DialogManager
from app.dialog.session import SessionStore
from app.nlu.hybrid import HybridNLU
from app.nlu.rules import RuleNLU
from app.search.engine import SearchEngine
from app.services.items import ItemService
from app.storage.db import init_db
from app.storage.importer import import_csv
from app.storage.repository import ItemRepository
from app.voice.stt import SpeechToText

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    repo: ItemRepository
    service: ItemService
    nlu: HybridNLU
    dialog: DialogManager
    stt: SpeechToText | None


def build(settings: Settings, clock: Clock | None = None) -> Container:
    clock = clock or Clock(settings.tz)
    repo = ItemRepository(init_db(settings.database_url))

    if repo.count(settings.default_user) == 0 and settings.seed_csv and Path(settings.seed_csv).exists():
        n = import_csv(repo, settings.seed_csv, settings.default_user, clock.now())
        log.info("Импортировано %s записей из %s", n, settings.seed_csv)

    embedder = None
    if settings.embeddings_enabled:
        from app.search.embeddings import OpenAICompatibleEmbedder

        embedder = OpenAICompatibleEmbedder(settings.embeddings_base_url, settings.embeddings_api_key,
                                            settings.embeddings_model)
    service = ItemService(repo, SearchEngine(embedder), clock)

    llm = None
    if settings.llm_enabled:
        from app.nlu.llm import LLMNLU

        llm = LLMNLU(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout)
    nlu = HybridNLU(RuleNLU(), llm)

    stt = SpeechToText(settings.stt_base_url, settings.stt_api_key, settings.stt_model) \
        if settings.stt_enabled else None
    dialog = DialogManager(nlu, service, SessionStore(), settings.default_user)
    return Container(settings, repo, service, nlu, dialog, stt)
