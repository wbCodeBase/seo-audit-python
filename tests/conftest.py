import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geo.models import Base  # noqa: E402


@event.listens_for(Engine, "connect")
def _sqlite_enforce_foreign_keys(dbapi_conn, _record):
    """SQLite ignores foreign keys unless asked; Postgres doesn't. Enforce
    them so insert-order bugs fail here, not on Supabase."""
    if type(dbapi_conn).__module__.startswith("sqlite3"):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


@pytest.fixture
def session():
    """Fresh in-memory SQLite DB per test. The schema is portable (JSON, not
    JSONB), so this exercises the same models that run on Postgres."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()
