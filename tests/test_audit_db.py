"""End-to-end over the DB layer with fake AI answers (no network)."""
from datetime import datetime, timezone

from sqlalchemy import func, select

from geo import audit
from geo.models import Answer, Citation, Competitor, Mention, Question, Run
from geo.parsing import ParsedAnswer
from geo.providers import ProviderResult

NOW = datetime(2026, 10, 16, 15, 30, tzinfo=timezone.utc)


def _check(text="netsuite partners noida", kind="keyword", competitors=("inoday.com",), **kw):
    c = {"kind": kind, "project": "SaasWorx", "domain": "saasworx.ai", "brand": "SaasWorx",
         "competitors": list(competitors), "text": text, "topic": None, "funnel_stage": None,
         "tags": [], "platforms": ["claude"]}
    c.update(kw)
    return c


def _result():
    return ProviderResult(
        model="claude-sonnet-5",
        usage={"input_tokens": 100, "output_tokens": 50},
        parsed=ParsedAnswer(
            grounded=True,
            answer_text="Inoday is well known. SaasWorx is also great. LinkedERP too.",
            rankings=[
                {"domain": "inoday.com", "title": "Inoday", "url": "https://inoday.com", "rank": 1,
                 "cited": True, "answer_position": 1},
                {"domain": "saasworx.ai", "title": "SaasWorx", "url": "https://saasworx.ai", "rank": 2,
                 "cited": True, "answer_position": 2},
            ],
            citations=[{"position": 1, "url": "https://inoday.com", "domain": "inoday.com", "title": "Inoday"},
                       {"position": 2, "url": "https://saasworx.ai", "domain": "saasworx.ai", "title": "SaasWorx"}],
        ),
    )


def _record(check, ctx, result, brands, repeat=1):
    from geo import discovery
    rec = {"check": check, "platform": "claude", "repeat": repeat, "result": result,
           "brands": brands, "extraction_error": "", "answer_id": f"a-{check['text']}-{repeat}",
           "intent": "local", "top1": "inoday.com"}
    rec["entities"] = audit.evaluate(check, ctx, result)
    known = discovery.KnownEntities()
    known.add(name=check["brand"], domain=check["domain"])
    for c in check["competitors"]:
        known.add(domain=c)
    rec["candidates"] = [] if result.error else discovery.discover(brands, result.parsed.citations, known)
    return rec


def test_evaluate_scores_and_mentions():
    ch = _check()
    ctx = audit._project_context(ch)
    rows = audit.evaluate(ch, ctx, _result())
    by = {r["entity_type"] + ":" + r["entity_name"]: r for r in rows}
    own, brand, comp = by["own:saasworx.ai"], by["own brand:SaasWorx"], by["competitor:inoday.com"]
    assert (own["rank"], own["score"], own["cited"], own["answer_position"]) == (2, 98, True, 2)
    assert own["mentioned"] and own["mention_position"] == 2
    assert brand["mention_position"] == 2
    assert (comp["rank"], comp["score"], comp["mention_position"]) == (1, 100, 1)


def test_evaluate_error_gives_single_own_row():
    ch = _check()
    rows = audit.evaluate(ch, audit._project_context(ch), ProviderResult(error="Rate limit"))
    assert len(rows) == 1 and rows[0]["entity_type"] == "own" and rows[0]["score"] is None


def test_full_cycle_sync_persist_review_resync(session):
    checks = [_check(), _check("Best NetSuite partner?", kind="question", topic="NetSuite",
                               funnel_stage="BOFU", tags=["partners"])]
    ctxs = audit.sync_config(session, checks, NOW)
    ctx = ctxs["SaasWorx"]
    assert len(ctx["question_ids"]) == 2
    assert [c["key"] for c in ctx["competitors"]] == ["inoday.com"]

    session.add(Run(id="run-1", trigger="cron", started_at=NOW))
    session.flush()   # audit.run() commits the run row before any answers exist
    brands = [{"name": "SaasWorx", "domain": ""}, {"name": "Inoday", "domain": ""},
              {"name": "LinkedERP", "domain": "linkederp.com"}]
    records = [_record(checks[0], ctx, _result(), brands, repeat=1),
               _record(checks[0], ctx, _result(), brands, repeat=2),
               _record(checks[1], ctx, ProviderResult(error="Rate limit"), None)]
    new = audit.persist(session, "run-1", records, ctxs, NOW)
    session.commit()

    assert new == 1                                                  # LinkedERP, once
    assert session.scalar(select(func.count()).select_from(Answer)) == 3
    assert session.scalar(select(func.count()).select_from(Mention)) == 6   # 3 entities × 2 ok answers
    assert session.scalar(select(func.count()).select_from(Citation)) == 4
    err = session.scalar(select(Answer).where(Answer.error.is_not(None)))
    assert err.mentions == [] and err.question_text == "Best NetSuite partner?"
    ok = session.scalar(select(Answer).where(Answer.repeat == 2))
    assert ok.model == "claude-sonnet-5" and ok.usage["input_tokens"] == 100 and ok.answer_text.startswith("Inoday")
    comp_m = session.scalar(select(Mention).where(Mention.entity_type == "competitor").limit(1))
    assert comp_m.competitor_id is not None

    linked = session.scalar(select(Competitor).where(Competitor.key == "linkederp.com"))
    assert (linked.relationship_, linked.source, linked.times_seen) == ("pending", "discovered", 2)

    # A human approves LinkedERP as a direct competitor and pauses the question.
    linked.relationship_ = "direct"
    session.commit()
    ctxs2 = audit.sync_config(session, [checks[0]], NOW)
    assert sorted(c["key"] for c in ctxs2["SaasWorx"]["competitors"]) == ["inoday.com", "linkederp.com"]
    q = session.scalar(select(Question).where(Question.kind == "question"))
    assert q.active is False and q.funnel_stage == "BOFU"

    # Now tracked → it gets its own mention row on the next answer.
    rows = audit.evaluate(checks[0], ctxs2["SaasWorx"], _result())
    linked_row = next(r for r in rows if r["competitor_key"] == "linkederp.com")
    assert linked_row["mentioned"] and linked_row["mention_position"] == 3 and linked_row["rank"] is None


def test_sheet_competitor_promotes_pending(session):
    ctxs = audit.sync_config(session, [_check(competitors=())], NOW)
    session.add(Competitor(client_id=ctxs["SaasWorx"]["client_id"], key="linkederp.com", name="LinkedERP",
                           domain="linkederp.com", relationship_="pending", source="discovered", times_seen=3))
    session.commit()
    audit.sync_config(session, [_check(competitors=("https://www.linkederp.com",))], NOW)
    c = session.scalar(select(Competitor).where(Competitor.key == "linkederp.com"))
    assert c.relationship_ == "direct" and c.times_seen == 3
