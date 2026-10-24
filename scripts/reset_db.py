"""Пересоздать базу и заново загрузить CSV: python scripts/reset_db.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.clock import Clock  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.storage.db import Base, make_engine  # noqa: E402
from app.storage.importer import import_csv  # noqa: E402
from app.storage.repository import ItemRepository  # noqa: E402

if __name__ == "__main__":
    s = get_settings()
    engine = make_engine(s.database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    repo = ItemRepository(sessionmaker(bind=engine, expire_on_commit=False))
    n = import_csv(repo, s.seed_csv, s.default_user, Clock(s.tz).now()) if s.seed_csv else 0
    print(f"База пересоздана: {s.database_url}. Загружено записей: {n}")
