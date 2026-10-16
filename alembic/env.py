import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from geo.config import load_dotenv
from geo.db import normalize_url
from geo.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

load_dotenv()
# Prefer the Supabase *session pooler* URL for DDL; the transaction pooler
# (DATABASE_URL on Vercel) doesn't suit long migration transactions.
url = (config.get_main_option("sqlalchemy.url")
       or os.environ.get("DATABASE_URL_DIRECT", "").strip()
       or os.environ.get("DATABASE_URL", "").strip())
if not url:
    raise SystemExit("Set DATABASE_URL_DIRECT or DATABASE_URL (in .env or the environment) before running alembic.")
url = normalize_url(url)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # profiles.id -> auth.users(id) only exists on Supabase (added by
    # migration 0002 when the auth schema is present), never in the models.
    if type_ == "foreign_key_constraint" and name == "profiles_id_fkey":
        return False
    return True


def run_migrations_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connect_args = {"prepare_threshold": None} if url.startswith("postgresql+psycopg://") else {}
    engine = create_engine(url, poolclass=NullPool, connect_args=connect_args)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
