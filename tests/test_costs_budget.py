"""Cost accounting, monthly budgets, question frequency and per-client engines."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from geo import costs, jobs
from geo.config import get_settings
from geo.db import get_engine, session_scope
from geo.models import Answer, Base, Client, Question, Run
from geo.parsing import ParsedAnswer
from geo.providers import ProviderResult

from tests.test_audit_db import _result


# ── pure cost maths ─────────────────────────────────────────────────────────

def test_api_cost_tokens_and_searches(monkeypatch):
    monkeypatch.delenv("MODEL_PRICES", raising=False)
    monkeypatch.delenv("SEARCH_FEE_PER_1K", raising=False)
    c = costs.api_cost("claude-sonnet-5", {"input_tokens": 1_000_000, "output_tokens": 100_000,
                                           "web_search_requests": 5})
    assert c == pytest.approx(3 + 1.5 + 0.05)
    # OpenAI: no search count reported → one search assumed
    assert costs.api_cost("gpt-5.1", {"input_tokens": 0, "output_tokens": 0}) == 0
    assert costs.api_cost("gpt-5.1", {"input_tokens": 1000, "output_tokens": 0}) == pytest.approx(0.00125 + 0.01)


def test_model_prices_override(monkeypatch):
    monkeypatch.setenv("MODEL_PRICES", '{"claude-sonnet-5": [1, 1]}')
    assert costs.api_cost("claude-sonnet-5", {"input_tokens": 1_000_000, "output_tokens": 0,
                                              "web_search_requests": 0}) == pytest.approx(1)


def test_answer_cost_observed_uses_billed_amount_plus_discovery():
    usage = {"discovery": {"model": "claude-haiku-5-5", "input_tokens": 1_000_000, "output_tokens": None}}
    assert costs.answer_cost("observed", "x", usage, 0.004) == pytest.approx(0.004 + 1)
    assert costs.answer_cost("observed", "x", {}, None) == 0


def test_plan_within_budget_keeps_observed_first():
    tasks = [{"app": "claude"}, {"app": "chatgpt_web"}, {"app": "google_aio"}, {"app": "chatgpt"}]
    # $0.05 left: observed 0.004 + 0.0026 go first, then chatgpt 0.04 fits; claude 0.08 doesn't
    kept, dropped = costs.plan_within_budget(tasks, budget=1.0, spent=0.95)
    assert [t["app"] for t in kept] == ["chatgpt_web", "google_aio", "chatgpt"]   # original order kept
    assert [t["app"] for t in dropped] == ["claude"]
    # $0.03 left: the cheap observed ones survive, both API ones are dropped
    kept, dropped = costs.plan_within_budget(tasks, budget=1.0, spent=0.97)
    assert [t["app"] for t in kept] == ["chatgpt_web", "google_aio"]
    assert costs.plan_within_budget(tasks, None, 999) == (tasks, [])


def test_month_start_is_ist():
    now = datetime(2026, 11, 30, 20, 0, tzinfo=timezone.utc)       # 1 Dec 01:30 IST
    assert costs.month_start_utc(now) == datetime(2026, 11, 30, 18, 30, tzinfo=timezone.utc)


# ── jobs with budgets / frequency / engines ─────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'b.db'}"
    Base.metadata.create_all(get_engine(url))
    calls = []

    def api_ask(q, country=None):
        calls.append(("api", q))
        return _result()

    def obs_ask(q, country=None):
        calls.append(("observed", q, country))
        r = _result()
        return ProviderResult(parsed=r.parsed, model="gpt-5-2", usage={"observed": {}}, cost_usd=0.004,
                              brands=[{"name": "Inoday", "domain": "inoday.com"}])

    providers = {"claude": {"tab": "C", "source": "api", "ask": api_ask},
                 "chatgpt_web": {"tab": "W", "source": "observed", "ask": obs_ask}}
    monkeypatch.setattr(jobs, "available_providers", lambda _s: providers)
    settings = replace(get_settings(), database_url=url, google_sheet_id="", discovery_enabled=True,
                       anthropic_api_key="would-be-used-for-discovery", repeats_per_run=1, sheets_dual_write=False)
    with session_scope(url) as s:
        c = Client(name="SaasWorx", domain="saasworx.ai", brand="SaasWorx", aliases=[], engines=[])
        s.add(c)
        s.flush()
        s.add_all([
            Question(client_id=c.id, kind="question", text="daily-q", tags=[], ai_apps=[], active=True,
                     frequency="daily", source="platform", country="in"),
            Question(client_id=c.id, kind="question", text="weekly-q", tags=[], ai_apps=[], active=True,
                     frequency="weekly", source="platform"),
        ])
        cid = c.id
    return {"url": url, "settings": settings, "cid": cid, "calls": calls}


def _set_client(env, **fields):
    with session_scope(env["url"]) as s:
        c = s.get(Client, env["cid"])
        for k, v in fields.items():
            setattr(c, k, v)


def test_observed_answer_saved_with_source_cost_and_free_discovery(env, monkeypatch):
    from geo import discovery
    monkeypatch.setattr(discovery, "extract_brands_llm",
                        lambda *a, **k: pytest.fail("observed answer brought its own brands"))
    st = env["settings"]
    _set_client(env, engines=["chatgpt_web"])
    started = jobs.start(env["cid"], settings=st, sync=False)
    assert {t["app"] for t in started["tasks"]} == {"chatgpt_web"}       # client engine list respected
    t = next(t for t in started["tasks"] if t["question"] == "daily-q")
    out = jobs.task(started["run_id"], t["question_id"], "chatgpt_web", 1, settings=st)
    assert out["source"] == "observed" and out["cost_usd"] == pytest.approx(0.004)
    assert ("observed", "daily-q", "in") in env["calls"]                 # question country passed through


def test_api_answer_cost_estimated(env, monkeypatch):
    from geo import discovery
    monkeypatch.setattr(discovery, "extract_brands_llm", lambda *a, **k: ([], {}))
    st = env["settings"]
    started = jobs.start(env["cid"], settings=st, sync=False)
    t = next(t for t in started["tasks"] if t["app"] == "claude")
    out = jobs.task(started["run_id"], t["question_id"], "claude", 1, settings=st)
    assert out["source"] == "api" and out["cost_usd"] > 0


def test_weekly_question_skipped_until_due_unless_forced(env, monkeypatch):
    from geo import discovery
    monkeypatch.setattr(discovery, "extract_brands_llm", lambda *a, **k: ([], {}))
    st = env["settings"]
    first = jobs.start(env["cid"], settings=st, sync=False)
    for t in first["tasks"]:
        jobs.task(first["run_id"], t["question_id"], t["app"], 1, settings=st)
    second = jobs.start(env["cid"], settings=st, sync=False)
    assert {t["question"] for t in second["tasks"]} == {"daily-q"} and second["skipped_not_due"] == 1
    forced = jobs.start(env["cid"], settings=st, sync=False, force=True)
    assert {t["question"] for t in forced["tasks"]} == {"daily-q", "weekly-q"}

    # 7 days later the weekly one is due again
    with session_scope(env["url"]) as s:
        for a in s.scalars(select(Answer)):
            a.created_at = a.created_at - timedelta(days=7)
    later = jobs.start(env["cid"], settings=st, sync=False)
    assert {t["question"] for t in later["tasks"]} == {"daily-q", "weekly-q"}


def test_budget_trims_run_and_blocks_tasks(env):
    st = env["settings"]
    with session_scope(env["url"]) as s:
        q = s.scalar(select(Question).where(Question.text == "daily-q"))
        s.add(Run(id="old", trigger="manual", status="done"))
        s.flush()
        s.add(Answer(id="spent", run_id="old", question_id=q.id, client_id=env["cid"], ai_app="claude",
                     source="api", cost_usd=0.995, question_text="x", usage={},
                     created_at=datetime.now(timezone.utc)))
    _set_client(env, monthly_budget_usd=1.0)
    started = jobs.start(env["cid"], settings=st, sync=False, force=True)
    # $0.005 left: only one $0.004 observed task fits; the $0.08 API tasks are dropped
    assert [t["app"] for t in started["tasks"]] == ["chatgpt_web"]
    assert started["budget"]["skipped_tasks"] == 3

    _set_client(env, monthly_budget_usd=0.995)
    blocked = jobs.start(env["cid"], settings=st, sync=False, force=True)
    assert blocked["run_id"] is None and "budget reached" in blocked["message"]
    with pytest.raises(jobs.JobError, match="budget"):
        jobs.task(started["run_id"], started["tasks"][0]["question_id"], "chatgpt_web", 1, settings=st)
    assert env["calls"] == []                                               # nothing was asked


def test_run_cannot_be_driven_for_another_client(env):
    st = env["settings"]
    started = jobs.start(env["cid"], settings=st, sync=False)
    t = started["tasks"][0]
    with pytest.raises(jobs.JobError, match="different client"):
        jobs.task(started["run_id"], t["question_id"], t["app"], 1, settings=st, client_id=env["cid"] + 1)
    with pytest.raises(jobs.JobError, match="different client"):
        jobs.finish(started["run_id"], metrics=False, settings=st, client_id=env["cid"] + 1)
    assert env["calls"] == []
