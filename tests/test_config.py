"""Настройки по умолчанию: локальный запуск и стенд на Amvera."""
from app import config

KEYS = ("AMVERA", "DATABASE_URL", "PUBLIC_DEMO", "LLM_PROVIDER", "GIGACHAT_CREDENTIALS")


def settings(monkeypatch, **env):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    try:
        return config.get_settings()
    finally:
        config.get_settings.cache_clear()


def test_local_defaults(monkeypatch):
    s = settings(monkeypatch)
    assert s.database_url.endswith("data/assistant.db") and not s.database_url.startswith("sqlite:////data")
    assert not s.public_demo and s.llm_provider == "openai" and not s.llm_enabled


def test_amvera_uses_persistent_disk_and_public_demo(monkeypatch):
    s = settings(monkeypatch, AMVERA="1")
    assert s.database_url == "sqlite:////data/assistant.db" and s.public_demo
    assert not settings(monkeypatch, AMVERA="1", PUBLIC_DEMO="0").public_demo


def test_gigachat_key_alone_selects_gigachat(monkeypatch):
    s = settings(monkeypatch, GIGACHAT_CREDENTIALS="key")
    assert s.llm_provider == "gigachat" and s.llm_enabled
    assert settings(monkeypatch, GIGACHAT_CREDENTIALS="key", LLM_PROVIDER="openai").llm_provider == "openai"


def test_quotes_around_gigachat_key_are_removed(monkeypatch):
    assert settings(monkeypatch, GIGACHAT_CREDENTIALS="'abc=='").gigachat_credentials == "abc=="
    assert settings(monkeypatch, GIGACHAT_CREDENTIALS='"abc=="').gigachat_credentials == "abc=="
