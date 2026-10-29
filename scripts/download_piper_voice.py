"""Скачать русский голос Piper для озвучки на сервере в папку models/piper/.

    python scripts/download_piper_voice.py            # Ирина (по умолчанию)
    python scripts/download_piper_voice.py denis      # другие голоса: denis, dmitri, ruslan

Голоса rhasspy/piper-voices с Hugging Face (~63 МБ каждый). Уже скачанный голос повторно не скачивается.
Другой голос подключается переменной TTS_PIPER_VOICE=models/piper/ru_RU-<имя>-medium.onnx.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

VOICES = ("irina", "denis", "dmitri", "ruslan")
BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/{name}/medium/{file}"
MODELS_DIR = Path(__file__).resolve().parent.parent / "models" / "piper"


def _download(url: str, target: Path) -> None:
    part = target.with_name(target.name + ".part")
    urllib.request.urlretrieve(url, part)
    part.replace(target)


def main(argv: list[str]) -> int:
    name = (argv[0] if argv else "irina").lower()
    if name not in VOICES:
        print(f"Неизвестный голос {name}. Есть: {', '.join(VOICES)}")
        return 1
    model = MODELS_DIR / f"ru_RU-{name}-medium.onnx"
    config = Path(f"{model}.json")
    if model.is_file() and config.is_file():
        print(f"Голос уже на месте: {model}")
        return 0
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for target in (config, model):  # сначала маленький файл настроек, потом сама модель
            url = BASE_URL.format(name=name, file=target.name)
            print(f"Скачиваю {url}")
            _download(url, target)
    except OSError as exc:
        print(f"Не получилось скачать голос: {exc}")
        for target in (config, model):
            target.unlink(missing_ok=True)
        return 1
    if model.stat().st_size < 1_000_000:  # вместо модели пришла страница с ошибкой
        print("Скачанный файл слишком мал — похоже, это не модель голоса")
        model.unlink()
        config.unlink(missing_ok=True)
        return 1
    print(f"Готово: {model}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
