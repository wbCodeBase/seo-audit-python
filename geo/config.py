"""
All runtime settings come from environment variables (see .env.example).
Nothing secret is ever hard-coded.
"""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_TRUE = {"1", "true", "yes", "y", "on"}


def load_dotenv(path=None):
    """Minimal .env loader for local runs (Vercel injects env vars itself).
    Never overrides a variable that is already set."""
    p = Path(path) if path else ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _flag(name, default):
    raw = _env(name)
    return default if raw == "" else raw.lower() in _TRUE


def _int(name, default):
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _list(name, default):
    raw = _env(name)
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else list(default)


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    openai_api_key: str
    claude_models: list
    openai_models: list
    google_sheet_id: str
    google_service_account_json: str
    database_url: str
    sheets_dual_write: bool
    repeats_per_run: int
    discovery_enabled: bool
    discovery_model: str
    sentiment_enabled: bool
    sentiment_model: str
    metrics_days: int
    max_workers: int


def get_settings():
    return Settings(
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        openai_api_key=_env("OPENAI_API_KEY"),
        claude_models=_list("CLAUDE_MODELS", ["claude-sonnet-5", "claude-opus-4-8"]),
        openai_models=_list("OPENAI_MODELS", ["gpt-5.1", "gpt-5", "gpt-4.1"]),
        google_sheet_id=_env("GOOGLE_SHEET_ID"),
        google_service_account_json=_env("GOOGLE_SERVICE_ACCOUNT_JSON"),
        database_url=_env("DATABASE_URL"),
        sheets_dual_write=_flag("SHEETS_DUAL_WRITE", True),
        repeats_per_run=max(1, _int("REPEATS_PER_RUN", 1)),
        discovery_enabled=_flag("DISCOVERY_ENABLED", True),
        discovery_model=_env("DISCOVERY_MODEL") or "claude-haiku-5-5",
        sentiment_enabled=_flag("SENTIMENT_ENABLED", True),
        sentiment_model=_env("SENTIMENT_MODEL") or "claude-haiku-5-5",
        metrics_days=max(1, _int("METRICS_DAYS", 7)),
        max_workers=max(1, _int("MAX_WORKERS", 6)),
    )
