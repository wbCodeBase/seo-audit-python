"""Job API (plan / start / task / finish) with fake AI apps and a real SQLite DB."""
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from geo import jobs
from geo.config import get_settings
from geo.db import get_engine, session_scope
from geo.models import Answer, Base, Client, Competitor, Question, Run
from geo.providers import ProviderResult

from tests.test_audit_db import _result


@pytest.fixture
def env(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'jobs.db'}"
    Base.metadata.create_all(get_engine(url))
    calls = {"n": 0, "fail": set()}

    def ask(q):
        calls["n"] += 1
        return ProviderResult(error="Rate limit") if q in calls["fail"] else _result()

    providers = {"claude": {"tab": "Results - Claude", "ask": ask},
                 "chatgpt": {"tab": "Results - ChatGPT", "ask": ask}}
    monkeypatch.setattr(jobs, "available_providers", lambda _s: providers)
    settings = replace(get_settings(), database_url=url, google_sheet_id="", discovery_enabled=False,
                       repeats_per_run=1, sheets_dual_write=True, anthropic_api_key="")
    with session_scope(url) as s:
        c = Client(name="SaasWorx", domain="saasworx.ai", brand="SaasWorx", aliases=[])
        s.add(c)
        s.flush()
        s.add_all([
            Question(client_id=c.id, kind="question", text="q-both", tags=[], ai_apps=[], active=True,
                     frequency="daily", source="platform"),
            Question(client_id=c.id, kind="question", text="q-claude", tags=[], ai_apps=["claude"], active=True,
                     frequency="daily", source="platform"),
            Question(client_id=c.id, kind="question", text="q-paused", tags=[], ai_apps=[], active=False,
                     frequency="daily", source="platform"),
            Competitor(client_id=c.id, key="inoday.com", name="inoday.com", domain="inoday.com",
                       relationship_="direct", source="sheet", times_seen=0),
        ])
        cid = c.id
    return {"url": url, "settings": settings, "client_id": cid, "calls": calls}


def test_plan_lists_clients_with_active_questions(env):
    out = jobs.plan(settings=env["settings"])
    assert out["clients"] == [{"id": env["client_id"], "name": "SaasWorx", "active_questions": 2}]
    assert out["sheet"] == "not configured"


def test_full_run(env):
    st = env["settings"]
    started = jobs.start(env["client_id"], settings=st, sync=False)
    tasks = started["tasks"]
    assert sorted((t["question"], t["app"]) for t in tasks) == [
        ("q-both", "chatgpt"), ("q-both", "claude"), ("q-claude", "claude")]   # paused skipped, apps respected
    rid = started["run_id"]

    results = [jobs.task(rid, t["question_id"], t["app"], t["repeat"], settings=st) for t in tasks]
    assert all(r["ok"] and not r["reused"] and r["named"] for r in results)
    assert results[0]["mention_position"] == 2           # "Inoday ... SaasWorx ..." in the fake answer

    again = jobs.task(rid, tasks[0]["question_id"], tasks[0]["app"], 1, settings=st)
    assert again["reused"] is True and env["calls"]["n"] == 3   # retry of a finished task costs nothing

    done = jobs.finish(rid, metrics=False, settings=st)
    assert done["status"] == "done" and done["stats"]["answers"] == 3 and done["stats"]["errors"] == 0
    assert done["sheet"] == "off"                          # no sheet configured in this test
    with session_scope(env["url"]) as s:
        assert s.get(Run, rid).status == "done"
        rows = jobs._sheet_rows_for_run(s, rid)
    assert set(rows) == {"claude", "chatgpt"} and len(rows["claude"]) == 6   # 2 answers × (own, brand, inoday)
    assert rows["claude"][0][2] == "SaasWorx" and rows["claude"][0][14] == rid


def test_failed_task_is_replaced_on_retry(env):
    st = env["settings"]
    started = jobs.start(env["client_id"], settings=st, sync=False)
    t = next(t for t in started["tasks"] if t["question"] == "q-claude")
    env["calls"]["fail"].add("q-claude")
    first = jobs.task(started["run_id"], t["question_id"], "claude", 1, settings=st)
    assert first["error"] == "Rate limit"
    env["calls"]["fail"].clear()
    second = jobs.task(started["run_id"], t["question_id"], "claude", 1, settings=st)
    assert second["error"] is None and second["reused"] is False
    with session_scope(env["url"]) as s:
        n = s.scalar(select(func.count()).select_from(Answer).where(Answer.run_id == started["run_id"]))
    assert n == 1                                         # the failed attempt was replaced, not duplicated
    out = jobs.finish(started["run_id"], metrics=False, settings=st)
    assert out["status"] == "partial"                     # 1 of 3 tasks done


def test_errors(env):
    st = env["settings"]
    with pytest.raises(jobs.JobError, match="No client"):
        jobs.start(99999, settings=st, sync=False)
    with pytest.raises(jobs.JobError, match="Unknown run"):
        jobs.task("nope", 1, "claude", settings=st)
    with pytest.raises(jobs.JobError, match="not configured"):
        jobs.task("nope", 1, "gemini", settings=st)
    with session_scope(env["url"]) as s:
        empty = Client(name="Empty", domain="empty.com", aliases=[])
        s.add(empty)
        s.flush()
        eid = empty.id
    out = jobs.start(eid, settings=st, sync=False)
    assert out["run_id"] is None and out["tasks"] == []
