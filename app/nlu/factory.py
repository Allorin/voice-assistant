"""Один способ выбора LLM для приложения и скриптов проверки."""
from __future__ import annotations

from app.config import Settings


def create_llm(settings: Settings):
    if not settings.llm_enabled:
        return None
    if settings.llm_provider == "gigachat":
        from app.nlu.gigachat import GigaChatNLU

        return GigaChatNLU(
            settings.gigachat_credentials, settings.gigachat_model, settings.gigachat_scope,
            settings.llm_timeout, settings.gigachat_ca_bundle_file or None,
        )
    from app.nlu.llm import LLMNLU

    return LLMNLU(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout)
