"""Резервная копия SQLite в приватный датасет Hugging Face (временный диск бесплатного Space)."""
import shutil
from dataclasses import replace
from pathlib import Path

from app.clock import FixedClock
from app.container import build
from app.models import ItemType, NewItem
from app.storage.backup import FILENAME, HubBackup, sqlite_file
from tests.conftest import NOW, make_settings


class FakeHub:
    """Датасет в папке: те же методы, что использует HubBackup у HfApi."""

    def __init__(self, folder: Path, fail: bool = False):
        self.folder = folder
        self.fail = fail
        self.uploads = 0
        self.private = None

    def create_repo(self, repo_id, repo_type, private, exist_ok):
        if self.fail:
            raise ConnectionError("нет сети")
        self.private = private

    def file_exists(self, repo_id, filename, repo_type):
        return (self.folder / filename).exists()

    def hf_hub_download(self, repo_id, filename, repo_type, local_dir):
        target = Path(local_dir) / filename
        shutil.copyfile(self.folder / filename, target)
        return str(target)

    def upload_file(self, path_or_fileobj, path_in_repo, repo_id, repo_type, commit_message):
        shutil.copyfile(path_or_fileobj, self.folder / path_in_repo)
        self.uploads += 1

    def super_squash_history(self, repo_id, repo_type):
        pass


def settings_for(db: Path):
    return replace(make_settings(seed=True), database_url=f"sqlite:///{db}")


def add_task(container, title):
    container.repo.add("demo", NewItem(type=ItemType.TASK, title=title), NOW)


def test_database_survives_space_restart(tmp_path):
    hub = FakeHub(tmp_path / "hub")
    hub.folder.mkdir()
    db = tmp_path / "space1" / "assistant.db"

    backup = HubBackup(db, "allorin/voice-assistant-data", api=hub, interval=3600)
    assert backup.restore() is False and hub.private is True  # первый запуск: копии ещё нет
    db.parent.mkdir()
    container = build(settings_for(db), FixedClock(NOW))
    add_task(container, "Купить корм коту")
    assert backup.flush() is True
    assert backup.flush() is False  # без изменений повторно не выгружается
    add_task(container, "Позвонить в банк")
    backup.stop()  # остановка Space: последние изменения выгружаются
    assert hub.uploads == 2

    db2 = tmp_path / "space2" / "assistant.db"  # новый контейнер, пустой диск
    restored = HubBackup(db2, "allorin/voice-assistant-data", api=hub)
    assert restored.restore() is True
    container2 = build(settings_for(db2), FixedClock(NOW))
    titles = {i.title for i in container2.repo.list_all("demo")}
    assert {"Купить корм коту", "Позвонить в банк"} <= titles
    assert container2.repo.count("demo") == 1002  # демо-данные не загружены повторно
    assert restored.flush() is False


def test_unreadable_backup_is_never_overwritten(tmp_path):
    hub = FakeHub(tmp_path / "hub", fail=True)
    db = tmp_path / "assistant.db"
    backup = HubBackup(db, "allorin/voice-assistant-data", api=hub)
    assert backup.restore() is False and backup.enabled is False
    container = build(settings_for(db), FixedClock(NOW))
    add_task(container, "Новая запись")
    assert backup.flush() is False and hub.uploads == 0


def test_backup_only_for_sqlite_file_with_settings():
    base = make_settings(seed=False)
    assert HubBackup.from_settings(base) is None
    ready = replace(base, database_url="sqlite:///data/assistant.db", backup_repo="a/b", hf_token="hf_x")
    assert HubBackup.from_settings(ready).repo_id == "a/b"
    assert HubBackup.from_settings(replace(ready, database_url="postgresql://u@h/db")) is None
    assert "hf_x" not in repr(ready)
    assert sqlite_file("sqlite:///data/assistant.db") == Path("data/assistant.db")
    assert sqlite_file("sqlite://") is None and FILENAME == "assistant.db"
