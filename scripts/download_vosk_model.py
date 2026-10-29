"""Скачать офлайн-модель распознавания русской речи Vosk в папку models/.

    python scripts/download_vosk_model.py

Модель vosk-model-small-ru-0.22 (~45 МБ, лицензия Apache 2.0) с alphacephei.com.
Если папка с моделью уже есть, повторно не скачивает.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

NAME = "vosk-model-small-ru-0.22"
URL = f"https://alphacephei.com/vosk/models/{NAME}.zip"
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def _progress(done: int, block: int, total: int) -> None:
    if total > 0 and sys.stdout.isatty():
        percent = min(100, done * block * 100 // total)
        print(f"\r  скачано {percent}%", end="", flush=True)


def main() -> int:
    target = MODELS_DIR / NAME
    if (target / "am").is_dir():
        print(f"Модель уже на месте: {target}")
        return 0
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Скачиваю {URL}")
    with tempfile.TemporaryDirectory(dir=MODELS_DIR) as tmp:
        archive = Path(tmp) / f"{NAME}.zip"
        try:
            urllib.request.urlretrieve(URL, archive, _progress)
        except OSError as exc:
            print(f"\nНе получилось скачать модель: {exc}")
            return 1
        print("\nРаспаковываю…")
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():  # архив должен содержать только папку модели
                if not member.startswith(f"{NAME}/") or ".." in Path(member).parts:
                    print(f"Неожиданный файл в архиве: {member}")
                    return 1
            zf.extractall(tmp)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(Path(tmp) / NAME), str(target))
    print(f"Готово: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
