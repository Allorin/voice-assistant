"""Один запрос напрямую в LLM, без подмены правилами и без изменений базы.

    python scripts/check_llm.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.nlu.factory import create_llm  # noqa: E402
from app.nlu.schema import Intent, NLUContext  # noqa: E402


def main() -> int:
    settings = get_settings()
    if not settings.llm_enabled:
        print("LLM пока не настроена. Для GigaChat заполните GIGACHAT_CREDENTIALS в .env "
              "и укажите LLM_PROVIDER=gigachat.")
        return 1
    llm = None
    try:
        llm = create_llm(settings)
        result = llm.parse("Напомни мне завтра в 10 утра позвонить Ивану",
                           NLUContext(now=datetime.now(settings.tz).replace(tzinfo=None)))
        if result.intent != Intent.CREATE or not result.title or not result.when:
            print("Модель ответила, но не извлекла все поля команды. Проверьте модель через eval_nlu.py.")
            return 1
        print(f"LLM ответила: {settings.active_llm_model}. Источник: {result.source}.")
        print(f"Действие: {result.intent.value}; название: {result.title}; время: {result.when}.")
        return 0
    except Exception as exc:
        # Не выводим тело HTTP-ответа и заголовки: в них могут находиться ключи и токены.
        # Сообщения вида «GigaChat: HTTP 401; …» уже очищены в app/nlu/gigachat.py — их показываем.
        reason = str(exc) if str(exc).startswith("GigaChat:") else type(exc).__name__
        print(f"Подключение не прошло ({reason}). Проверьте ключ, scope, сеть и TLS-сертификаты.")
        print("При ошибке сертификата задайте GIGACHAT_CA_BUNDLE_FILE: путь к PEM-файлу "
              "с официальным корневым сертификатом НУЦ Минцифры.")
        return 1
    finally:
        if llm is not None:
            llm.close()


if __name__ == "__main__":
    sys.exit(main())
