import os
from datetime import datetime
from pathlib import Path

import pytest

from app.clock import FixedClock
from app.config import Settings
from app.container import build

ROOT = Path(__file__).resolve().parent.parent
# «Сейчас» во всех тестах: вторник, 20 октября 2026, 19:59
NOW = datetime(2026, 10, 20, 19, 59)


# TEST_DATABASE_URL=postgresql://… — прогнать те же тесты на Postgres (каждый тест на пустой схеме)
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


def _fresh_database() -> str:
    if not TEST_DATABASE_URL:
        return "sqlite://"
    from app.storage.db import Base, make_engine

    engine = make_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)
    engine.dispose()
    return TEST_DATABASE_URL


def make_settings(seed: bool = True) -> Settings:
    return Settings(
        database_url=_fresh_database(), timezone="Europe/Moscow",
        seed_csv=str(ROOT / "data" / "events_seed.csv") if seed else "", default_user="demo",
        llm_base_url="", llm_api_key="", llm_model="", llm_timeout=5,
        stt_base_url="", stt_api_key="", stt_model="",
        embeddings_base_url="", embeddings_api_key="", embeddings_model="",
    )


@pytest.fixture
def container():
    return build(make_settings(seed=True), FixedClock(NOW))


@pytest.fixture
def empty_container():
    return build(make_settings(seed=False), FixedClock(NOW))


@pytest.fixture
def say(container):
    """say("текст") -> Reply в одной и той же сессии."""
    return lambda text: container.dialog.handle("test", text)


@pytest.fixture
def say_empty(empty_container):
    return lambda text: empty_container.dialog.handle("test", text)
