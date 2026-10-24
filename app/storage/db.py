"""Схема БД (SQLAlchemy). По умолчанию SQLite, через DATABASE_URL можно подключить Postgres."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class ItemRow(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str] = mapped_column(Text, default="")
    remind_before_min: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    has_time: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(16), default="user")
    recurrence: Mapped[str | None] = mapped_column(String(200), nullable=True)   # RRULE для повторяющихся
    done_dates: Mapped[str] = mapped_column(Text, default="")                  # «2026-10-26,2026-11-02»
    skipped_dates: Mapped[str] = mapped_column(Text, default="")               # исключённые из серии даты (EXDATE)
    series_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # перенесённое повторение
    original_date: Mapped[str | None] = mapped_column(String(10), nullable=True)      # … какой даты серии


# Колонки, добавленные после первой версии: для уже существующей базы досоздаются при старте
_ADDED_COLUMNS = {
    "notes": "TEXT DEFAULT ''",
    "remind_before_min": "INTEGER DEFAULT 0",
    "recurrence": "VARCHAR(200)",
    "done_dates": "TEXT DEFAULT ''",
    "skipped_dates": "TEXT DEFAULT ''",
    "series_id": "INTEGER",
    "original_date": "VARCHAR(10)",
}


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool  # одна БД в памяти на все потоки (тесты)
    return create_engine(url, **kwargs)


def _migrate(engine: Engine) -> None:
    existing = {c["name"] for c in inspect(engine).get_columns("items")}
    with engine.begin() as conn:
        for name, ddl in _ADDED_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE items ADD COLUMN {name} {ddl}"))
                logging.getLogger(__name__).info("БД: добавлена колонка items.%s", name)


def init_db(url: str) -> sessionmaker:
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    _migrate(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
