"""Резервная копия SQLite в приватном датасете Hugging Face.

Диск бесплатного Space временный: после перезапуска файла базы нет. Поэтому база работает на локальном
диске как обычно, а её копия выгружается в приватный датасет раз в минуту (если были изменения) и при
остановке сервера. При старте, если файла нет, скачивается последняя копия. Отдельная облачная база
не нужна: хватает аккаунта Hugging Face, на котором уже работает стенд.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)
FILENAME = "assistant.db"
AUTO_REPO = "auto"            # BACKUP_REPO=auto → <владелец токена>/voice-assistant-data
DEFAULT_DATASET = "voice-assistant-data"


def sqlite_file(url: str) -> Path | None:
    """Путь к файлу базы из sqlite:///…; для Postgres и базы в памяти — None."""
    prefix = "sqlite:///"
    if not url.startswith(prefix) or url in ("sqlite:///:memory:",):
        return None
    return Path(url[len(prefix):])


class HubBackup:
    SQUASH_EVERY = 100  # каждая выгрузка — коммит; историю датасета время от времени сжимаем

    def __init__(self, db_path: Path, repo_id: str, token: str | None = None, interval: float = 60.0, api=None):
        self._api = api
        self._token = token
        self.db_path = Path(db_path)
        self.repo_id = repo_id
        self.interval = interval
        self.enabled = True
        self._last_hash: str | None = None
        self._uploads = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def api(self):
        if self._api is None:  # huggingface_hub нужен только на стенде (requirements-docker.txt)
            from huggingface_hub import HfApi

            self._api = HfApi(token=self._token)
        return self._api

    @classmethod
    def from_settings(cls, settings) -> HubBackup | None:
        path = sqlite_file(settings.database_url)
        if not (settings.backup_repo and settings.hf_token and path):
            return None
        return cls(path, settings.backup_repo, settings.hf_token)

    # ------------------------------------------------------------------ старт

    def restore(self) -> bool:
        """Скачать последнюю копию, если локальной базы нет. True — база восстановлена."""
        try:
            if self.repo_id == AUTO_REPO:
                self.repo_id = f"{self.api.whoami()['name']}/{DEFAULT_DATASET}"
            self.api.create_repo(self.repo_id, repo_type="dataset", private=True, exist_ok=True)
            if self.db_path.exists():
                self._last_hash = self._snapshot_hash()
                return False
            if not self.api.file_exists(self.repo_id, FILENAME, repo_type="dataset"):
                log.info("Резервной копии в %s ещё нет: база создаётся заново", self.repo_id)
                return False
            with tempfile.TemporaryDirectory() as tmp:
                downloaded = self.api.hf_hub_download(repo_id=self.repo_id, filename=FILENAME,
                                                      repo_type="dataset", local_dir=tmp)
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(downloaded, self.db_path)
            self._last_hash = self._snapshot_hash()
            log.info("База восстановлена из резервной копии %s", self.repo_id)
            return True
        except Exception as exc:
            # Копию не удалось прочитать: выгрузка выключается, чтобы пустая база её не затёрла
            self.enabled = False
            log.error("Резервная копия недоступна (%s), сохранение в датасет выключено", type(exc).__name__)
            return False

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="db-backup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self.flush()

    # ------------------------------------------------------------------ выгрузка

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.flush()

    def flush(self) -> bool:
        """Выгрузить копию, если база изменилась с прошлой выгрузки. True — выгружено."""
        if not self.enabled or not self.db_path.exists() or self.repo_id == AUTO_REPO:
            return False
        with self._lock:
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    snapshot = Path(tmp) / FILENAME
                    self._snapshot(snapshot)
                    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
                    if digest == self._last_hash:
                        return False
                    self.api.upload_file(path_or_fileobj=str(snapshot), path_in_repo=FILENAME,
                                         repo_id=self.repo_id, repo_type="dataset",
                                         commit_message="Резервная копия базы")
                self._last_hash = digest
                self._uploads += 1
                if self._uploads % self.SQUASH_EVERY == 0:
                    self.api.super_squash_history(repo_id=self.repo_id, repo_type="dataset")
                return True
            except Exception as exc:
                log.warning("Не удалось сохранить резервную копию базы: %s", type(exc).__name__)
                return False

    def _snapshot(self, target: Path) -> None:
        """Согласованная копия работающей базы (SQLite backup API, без остановки записи)."""
        src = sqlite3.connect(self.db_path)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    def _snapshot_hash(self) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / FILENAME
            self._snapshot(snapshot)
            return hashlib.sha256(snapshot.read_bytes()).hexdigest()
