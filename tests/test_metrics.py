from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from geo import metrics
from geo.metrics import answer_facts, summarize, summarize_competitors, visibility_score
from geo.models import (
    Answer, Client, Competitor, DailyCompetitorMetric, DailyMetric, Mention, Question, Run,
)

DIRECT = {1: "direct", 2: "direct", 3: "indirect"}


def M(etype, named, cited=False, score=None, comp=None):
    return NS(entity_type=etype, mentioned=named, cited=cited, score=score, competitor_id=comp)


def A(mentions, sentiment=None, text="answer"):
    return NS(mentions=mentions, sentiment=sentiment, answer_text=text)


def _facts():
    return [
        # 1: we're named + cited, one direct competitor named
        answer_facts(A([M("own", True, True, 98), M("own brand", True, False, 88),
                        M("competitor", True, False, 100, comp=1)], sentiment=80), DIRECT),
        # 2: we're missing, two direct competitors named
        answer_facts(A([M("own", False, False, 0), M("competitor", True, comp=1),
                        M("competitor", True, comp=2)]), DIRECT),
        # 3: nobody named, AI didn't search (no score)
        answer_facts(A([M("own", False), M("competitor", False, comp=1)]), DIRECT),
        # 4: we're named; only an INDIRECT competitor named alongside
        answer_facts(A([M("own", True, False, 100), M("competitor", True, comp=3)], sentiment=60), DIRECT),
    ]


def test_qwairy_definitions():
    r = summarize(_facts())
    assert r["answers"] == 4
    assert r["answers_naming_us"] == 2
    assert r["answers_naming_us_or_direct"] == 3                 # answers 1, 2, 4
    assert r["mention_rate"] == pytest.approx(200 / 3)
    assert r["direct_competitor_mentions"] == 3                  # 1 + 2; indirect excluded
    assert r["share_of_voice"] == pytest.approx(40.0)            # 2 / (2 + 3)
    assert r["position_score"] == pytest.approx((98 + 0 + 100) / 3)   # best of own/brand; ungrounded skipped
    assert r["citation_rate"] == pytest.approx(25.0)
    assert r["sentiment"] == pytest.approx(70.0)
    assert r["covered"] is True and r["estimated"] is False
    assert r["visibility_score"] == pytest.approx(
        0.40 * 200 / 3 + 0.25 * 66 + 0.20 * 40 + 0.15 * 70, abs=0.01)


def test_visibility_waits_for_sentiment_when_we_are_named():
    facts = _facts()
    facts[3]["sentiment"] = None                                 # named, has text, not scored yet
    r = summarize(facts)
    assert r["sentiment_pending"] == 1 and r["visibility_score"] is None
    assert r["mention_rate"] is not None                         # everything else still computed


def test_never_named_gives_zero_visibility_not_blank():
    f = [answer_facts(A([M("own", False, False, 0), M("competitor", True, comp=1)]), DIRECT)]
    r = summarize(f)
    assert (r["mention_rate"], r["share_of_voice"], r["covered"]) == (0.0, 0.0, False)
    assert r["sentiment"] is None and r["visibility_score"] == 0.0


def test_nobody_named_means_undefined_rates():
    r = summarize([answer_facts(A([M("own", False)]), DIRECT)])
    assert r["mention_rate"] is None and r["share_of_voice"] is None and r["position_score"] is None
    assert r["visibility_score"] == 0.0


def test_imported_history_uses_cited_as_proxy_and_is_flagged():
    f = [answer_facts(A([M("own", None, True, 100), M("competitor", None, False, 50, comp=1)], text=None),
                      DIRECT)]
    r = summarize(f)
    assert r["answers_naming_us"] == 1 and r["estimated"] is True
    assert r["sentiment_pending"] == 0                           # no text → can't be scored → not pending
    # sentiment left out, remaining weights rescaled to 100%: (40+25+20)/0.85
    assert r["visibility_score"] == pytest.approx(100.0)
    r2 = summarize([answer_facts(A([M("own", None, True, 50)], text=None), DIRECT)])
    assert r2["visibility_score"] == pytest.approx((0.40 * 100 + 0.25 * 50 + 0.20 * 100) / 0.85, abs=0.01)


def test_visibility_formula_weights():
    assert visibility_score(100, 100, 100, 100, 0) == 100
    assert visibility_score(50, 0, 0, 0, 0) == 20
    assert visibility_score(None, None, None, None, 0) == 0
    assert visibility_score(100, 100, 100, None, 2) is None
    assert visibility_score(100, 100, 100, None, 0, sentiment_unknowable=True) == 100
    assert visibility_score(100, 100, 100, None, 0) == 85            # never named → sentiment counts as 0


def test_competitor_rollup():
    out = summarize_competitors(_facts())
    assert out[1] == {"answers": 4, "mentions": 2, "cited": 0, "position_sum": 100.0, "position_n": 1}
    assert out[2]["mentions"] == 1 and out[3]["mentions"] == 1


# ── compute() against the DB ────────────────────────────────────────────────

def _seed(session):
    c = Client(name="SaasWorx", domain="saasworx.ai", brand="SaasWorx", aliases=[])
    session.add(c)
    session.flush()
    q = Question(client_id=c.id, kind="keyword", text="kw", tags=[], ai_apps=[], active=True, frequency="daily")
    comp = Competitor(client_id=c.id, key="inoday.com", name="inoday.com", domain="inoday.com",
                      relationship_="direct", source="sheet", times_seen=0)
    session.add_all([q, comp, Run(id="r1", trigger="cron", started_at=datetime(2026, 10, 1, tzinfo=timezone.utc))])
    session.flush()

    def answer(aid, ts, us_named, comp_named, app="claude", error=None):
        a = Answer(id=aid, run_id="r1", question_id=q.id, client_id=c.id, ai_app=app, repeat=1,
                   question_text="kw", grounded=True, usage={}, created_at=ts, error=error, answer_text="x",
                   sentiment=70 if us_named else None)
        session.add(a)
        session.flush()
        if not error:
            session.add_all([
                Mention(answer_id=aid, client_id=c.id, entity_type="own", entity_name="saasworx.ai",
                        mentioned=us_named, cited=us_named, score=100 if us_named else 0),
                Mention(answer_id=aid, client_id=c.id, competitor_id=comp.id, entity_type="competitor",
                        entity_name="inoday.com", mentioned=comp_named, cited=False, score=88),
            ])
    # 2026-10-15 18:00 UTC = 2026-10-15 23:30 IST; 19:00 UTC = 2026-10-16 00:30 IST → next IST day
    answer("a1", datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc), True, True)
    answer("a2", datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc), False, True)
    answer("a3", datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc), True, False, app="chatgpt")
    answer("a4", datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc), True, True, error="Rate limit")
    answer("old", datetime(2026, 9, 1, tzinfo=timezone.utc), True, False)
    session.flush()
    return c, q, comp


def test_compute_groups_by_ist_day_and_app(session):
    _seed(session)
    stats = metrics.compute(session, days=7, today=date(2026, 10, 16))
    assert stats["metric_rows"] == 3 and stats["from"] == "2026-10-10"
    rows = {(r.day.isoformat(), r.ai_app): r for r in session.scalars(select(DailyMetric))}
    assert set(rows) == {("2026-10-15", "claude"), ("2026-10-16", "claude"), ("2026-10-16", "chatgpt")}
    assert rows[("2026-10-15", "claude")].mention_rate == 100.0
    assert rows[("2026-10-16", "claude")].mention_rate == 0.0 and rows[("2026-10-16", "claude")].covered is False
    assert rows[("2026-10-16", "chatgpt")].answers == 1                 # errored a4 excluded
    assert rows[("2026-10-15", "claude")].visibility_score is not None
    comp_rows = session.scalars(select(DailyCompetitorMetric)).all()
    assert sum(r.mentions for r in comp_rows) == 2


def test_compute_is_idempotent_and_only_touches_window(session):
    c, q, _ = _seed(session)
    session.add(DailyMetric(day=date(2026, 9, 1), client_id=c.id, question_id=q.id, ai_app="claude",
                            answers=1, answers_naming_us=1, answers_naming_us_or_direct=1,
                            direct_competitor_mentions=0, answers_citing_us=1, position_sum=0,
                            position_n=0, sentiment_sum=0, sentiment_n=0, sentiment_pending=0,
                            covered=True, estimated=False))
    session.flush()
    metrics.compute(session, days=7, today=date(2026, 10, 16))
    metrics.compute(session, days=7, today=date(2026, 10, 16))
    days = sorted(r.day.isoformat() for r in session.scalars(select(DailyMetric)))
    assert days == ["2026-09-01", "2026-10-15", "2026-10-16", "2026-10-16"]   # old row kept, no dupes
