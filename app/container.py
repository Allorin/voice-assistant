"""Сборка зависимостей приложения в одном месте (простой DI без фреймворков)."""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.clock import Clock
from app.config import Settings
from app.dialog.manager import DialogManager
from app.dialog.session import SessionStore
from app.nlu.factory import create_llm
from app.nlu.hybrid import HybridNLU
from app.nlu.rules import RuleNLU
from app.search.engine import SearchEngine
from app.services.items import ItemService
from app.storage.db import init_db
from app.storage.importer import import_csv
from app.storage.repository import ItemRepository
from app.voice.stt import SpeechToText

log = logging.getLogger(__name__)


USER_ID_RX = re.compile(r"^[\w.@-]{1,64}$")  # буквы любого алфавита, цифры, . @ - _


@dataclass
class Container:
    settings: Settings
    repo: ItemRepository
    service: ItemService
    nlu: HybridNLU
    dialog: DialogManager
    stt: SpeechToText | None
    _known_users: set[str] = field(default_factory=set)
    _users_lock: threading.Lock = field(default_factory=threading.Lock)

    def prepare_user(self, user_id: str) -> None:
        """Первый запрос профиля: запомнить его, а на публичном стенде выдать копию демо-записей."""
        if user_id in self._known_users:
            return
        with self._users_lock:
            if user_id in self._known_users:
                return
            now = self.service.now()
            created = self.repo.ensure_user(user_id, now)
            if created and self.settings.public_demo and self.repo.count(user_id) == 0:
                self._seed(user_id, now)
            self._known_users.add(user_id)

    def _seed(self, user_id: str, now) -> None:
        csv_path = self.settings.seed_csv
        if csv_path and Path(csv_path).exists():
            n = import_csv(self.repo, csv_path, user_id, now)
            log.info("Профиль %s: загружено %s демо-записей", user_id, n)


def build(settings: Settings, clock: Clock | None = None) -> Container:
    clock = clock or Clock(settings.tz)
    repo = ItemRepository(init_db(settings.database_url))

    repo.ensure_user(settings.default_user, clock.now())
    if repo.count(settings.default_user) == 0 and settings.seed_csv and Path(settings.seed_csv).exists():
        n = import_csv(repo, settings.seed_csv, settings.default_user, clock.now())
        log.info("Импортировано %s записей из %s", n, settings.seed_csv)

    embedder = None
    if settings.embeddings_enabled:
        from app.search.embeddings import OpenAICompatibleEmbedder

        embedder = OpenAICompatibleEmbedder(settings.embeddings_base_url, settings.embeddings_api_key,
                                            settings.embeddings_model)
    service = ItemService(repo, SearchEngine(embedder), clock)

    try:
        llm = create_llm(settings)
    except Exception as exc:  # битый сертификат или настройки LLM не должны мешать запуску на правилах
        log.warning("LLM не подключена, работают правила: %s", exc)
        llm = None
    nlu = HybridNLU(RuleNLU(), llm)

    stt = SpeechToText(settings.stt_base_url, settings.stt_api_key, settings.stt_model) \
        if settings.stt_enabled else None
    dialog = DialogManager(nlu, service, SessionStore(), settings.default_user)
    return Container(settings, repo, service, nlu, dialog, stt)
