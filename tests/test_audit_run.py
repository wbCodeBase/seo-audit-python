"""audit.run() glue: sheet in → AI apps → DB + Results tabs, with every
external service faked."""
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from geo import audit, discovery, sheets_io
from geo.config import get_settings
from geo.db import get_engine, session_scope
from geo.models import Answer, Base, Competitor, Run

from tests.test_audit_db import _check, _result


@pytest.fixture
def fakes(monkeypatch):
    written = {}
    monkeypatch.setattr(sheets_io, "sheets_service", lambda _j: object())
    monkeypatch.setattr(sheets_io, "read_checks", lambda _svc, _sid, _known: (
        [_check()], [{"tab": "Keywords", "row": 3, "text": "paused", "active": "No"}]))
    monkeypatch.setattr(sheets_io, "ensure_results_header", lambda *_a: None)
    monkeypatch.setattr(sheets_io, "append_results", lambda _svc, _sid, tab, rows: written.__setitem__(tab, rows))
    monkeypatch.setattr(audit, "available_providers", lambda _s: {
        "claude": {"tab": "Results - Claude", "ask": lambda _q: _result()}})
    monkeypatch.setattr(discovery, "extract_brands_llm", lambda *_a, **_k: (
        [{"name": "LinkedERP", "domain": "linkederp.com"}], {"model": "haiku", "input_tokens": 5}))
    return written


def _settings(**kw):
    base = get_settings()
    return replace(base, google_sheet_id="sheet", anthropic_api_key="k", google_service_account_json="{}",
                   repeats_per_run=2, discovery_enabled=True, **kw)


def test_run_writes_db_and_sheet(fakes, tmp_path):
    url = f"sqlite:///{tmp_path / 'geo.db'}"
    Base.metadata.create_all(get_engine(url))

    out = audit.run(trigger="manual", settings=_settings(database_url=url, sheets_dual_write=True))

    assert out["ok"] and out["saved_to_database"] and "db_error" not in out
    assert out["answers"] == 2 and out["new_competitor_candidates"] == 1
    assert out["skipped"][0]["text"] == "paused"
    rows = fakes["Results - Claude"]
    assert len(rows) == 6                                   # 2 repeats × (own, brand, inoday)
    assert {r[14] for r in rows} == {out["run_id"]}         # Run ID column
    assert sorted({r[19] for r in rows}) == [1, 2]          # Repeat #
    with session_scope(url) as s:
        run = s.get(Run, out["run_id"])
        assert run.status == "done" and run.trigger == "manual" and run.stats["answers"] == 2
        assert s.scalar(select(func.count()).select_from(Answer)) == 2
        a = s.scalar(select(Answer).limit(1))
        assert a.usage["discovery"]["model"] == "haiku"
        assert s.scalar(select(Competitor).where(Competitor.key == "linkederp.com")).relationship_ == "pending"


def test_run_sheet_only_when_dual_write_off_and_no_db(fakes):
    out = audit.run(settings=_settings(database_url="", sheets_dual_write=False))
    assert out["ok"] and not out["saved_to_database"]
    assert len(fakes["Results - Claude"]) == 6               # no DB → sheet is always written


def test_db_down_falls_back_to_sheet(fakes, tmp_path):
    url = f"sqlite:///{tmp_path / 'missing' / 'nope.db'}"   # directory doesn't exist → connect fails
    out = audit.run(settings=_settings(database_url=url, sheets_dual_write=False))
    assert out["ok"] and not out["saved_to_database"] and out["db_error"]
    assert len(fakes["Results - Claude"]) == 6
