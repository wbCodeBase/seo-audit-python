"""
Database engine/session.

Supabase notes:
  - From Vercel (serverless) use the *Transaction pooler* URL (port 6543).
    PgBouncer in transaction mode can't keep prepared statements across
    transactions, so they're disabled (prepare_threshold=None), and NullPool
    is used so each invocation opens/closes its own connection rather than
    holding one in a pool that the platform will freeze.
  - Migrations (Alembic) should use DATABASE_URL_DIRECT — the *Session
    pooler* URL (port 5432) — see alembic/env.py.
"""
from contextlib import contextmanager
from urllib.parse import quote, unquote

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

_engines = {}


def _encode_userinfo(url):
    """Passwords containing '@', ':', '/' or '#' break URL parsing when
    pasted raw into a connection string (the host gets read as
    'rest-of-password@host'). Split at the LAST '@' before the host and
    percent-encode user and password. Already-encoded values are left as-is."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    # Hostnames never contain '@', so the last '@' separates userinfo from host.
    at = rest.rfind("@")
    if at == -1:
        return url
    userinfo, hostpart = rest[:at], rest[at + 1:]
    user, colon, password = userinfo.partition(":")
    enc = lambda s: quote(unquote(s), safe="")
    return f"{scheme}://{enc(user)}{':' + enc(password) if colon else ''}@{hostpart}"


def normalize_url(url):
    """Supabase hands out postgres:// / postgresql:// URLs; SQLAlchemy needs
    the driver named explicitly to use psycopg 3."""
    url = _encode_userinfo(url.strip())
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine(url):
    url = normalize_url(url)
    if url not in _engines:
        kwargs = {"future": True}
        if url.startswith("postgresql+psycopg://"):
            kwargs["poolclass"] = NullPool
            kwargs["connect_args"] = {"prepare_threshold": None}
        _engines[url] = create_engine(url, **kwargs)
    return _engines[url]


@contextmanager
def session_scope(url):
    """Commit on success, roll back on any error."""
    session = sessionmaker(bind=get_engine(url), expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
