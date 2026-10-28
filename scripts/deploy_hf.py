"""Выкладка прототипа на Hugging Face Spaces (Docker).

Запуск: deploy_hf.bat на Windows, `python scripts/deploy_hf.py` или GitHub Actions после тестов.
Настройки берутся из окружения или из .env:
- HF_TOKEN — токен Hugging Face с правом записи (обязательно);
- HF_SPACE — имя Space, по умолчанию voice-assistant;
- GIGACHAT_CREDENTIALS — ключ GigaChat (без него стенд разбирает запросы правилами);
- DEPLOY_DATABASE_URL — необязательно: внешний Postgres для стенда (например, Neon). Без него стенд работает
  на SQLite, а копия базы хранится в приватном датасете <пользователь>/<space>-data того же аккаунта
  (токен передаётся стенду секретом HF_TOKEN). Отдельное имя, чтобы локальный запуск остался на своём SQLite.
Секреты передаются в настройки Space и не попадают в его файлы.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# В Space нужно только то, из чего собирается образ, и README
FILES = ["Dockerfile", ".dockerignore", "requirements.txt", "requirements-postgres.txt", "requirements-docker.txt",
         "README.md",
         "data/events_seed.csv"]
FOLDERS = ["app", "certs"]
FRONT_MATTER = """---
title: Голосовой ежедневник
emoji: 📅
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: Голосовой ассистент для задач, напоминаний и встреч
---

"""


def space_url(repo_id: str) -> str:
    return "https://" + re.sub(r"[^a-z0-9-]", "-", repo_id.replace("/", "-").lower()) + ".hf.space"


def collect(target: Path) -> None:
    for name in FILES:
        if (ROOT / name).exists():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target / name)
    for folder in FOLDERS:
        if (ROOT / folder).is_dir():
            shutil.copytree(ROOT / folder, target / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db"))
    readme = target / "README.md"
    readme.write_text(FRONT_MATTER + readme.read_text(encoding="utf-8"), encoding="utf-8")


def main() -> int:
    token = os.getenv("HF_TOKEN")
    if not token:
        print("HF_TOKEN не задан: добавьте в .env строку HF_TOKEN=… (токен Hugging Face с правом Write).")
        return 1
    api = HfApi(token=token)
    owner = os.getenv("HF_OWNER") or api.whoami()["name"]
    repo_id = f"{owner}/{os.getenv('HF_SPACE') or 'voice-assistant'}"
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)

    gigachat = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    database = os.getenv("DEPLOY_DATABASE_URL", "").strip()
    if gigachat:
        api.add_space_secret(repo_id, "GIGACHAT_CREDENTIALS", gigachat)
    backup_repo = ""
    if database:
        api.add_space_secret(repo_id, "DATABASE_URL", database)
        try:
            api.delete_space_variable(repo_id, "BACKUP_REPO")
        except Exception:  # переменной ещё не было
            pass
    else:
        # без внешней базы: SQLite на диске Space + копия в приватном датасете (app/storage/backup.py)
        backup_repo = f"{repo_id}-data"
        api.create_repo(backup_repo, repo_type="dataset", private=True, exist_ok=True)
        api.add_space_secret(repo_id, "HF_TOKEN", token)
        api.add_space_variable(repo_id, "BACKUP_REPO", backup_repo)
    variables = {"PUBLIC_DEMO": "1", "TIMEZONE": "Europe/Moscow", "LLM_TIMEOUT": "10",
                 "LLM_PROVIDER": "gigachat" if gigachat else "openai"}
    for key, value in variables.items():
        api.add_space_variable(repo_id, key, value)

    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True).stdout.strip() or "local"
    except OSError:  # git не установлен
        sha = "local"
    with tempfile.TemporaryDirectory() as tmp:
        collect(Path(tmp))
        api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=tmp, delete_patterns="*",
                          commit_message=f"Версия {sha}")
    print(f"Space: https://huggingface.co/spaces/{repo_id}")
    print(f"Прототип: {space_url(repo_id)}")
    db = ("Postgres из DEPLOY_DATABASE_URL" if database
          else f"SQLite, копия раз в минуту в приватном датасете https://huggingface.co/datasets/{backup_repo}")
    print(f"База: {db}")
    print(f"LLM: {'GigaChat' if gigachat else 'нет, работают правила'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
