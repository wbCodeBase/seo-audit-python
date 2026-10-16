"""
Nightly roll-up of answers into daily_metrics / daily_competitor_metrics,
per client × question × AI app × day (IST).

Definitions (Qwairy-style), over the successful answers in a group:
  mention rate     = answers naming us ÷ answers naming us OR a direct competitor
  share of voice   = answers naming us ÷ (answers naming us + direct-competitor
                     mentions, one per competitor per answer)
  position score   = average of the existing rank→score rule (geo.scoring)
                     for us — best of our domain and brand rows — over
                     answers where the AI actually searched
  citation rate    = answers citing our domain/brand ÷ answers
  covered          = we were named at least once
  sentiment        = average answers.sentiment (0–100) where we're named
  visibility score = 0.40×mention + 0.25×position + 0.20×SoV + 0.15×sentiment

All rates are 0–100. Missing components count as 0 in the visibility score
(not named → nothing to be visible with), with two exceptions for sentiment
on answers that DO name us:
  - not scored yet (no API key, or the job hasn't run): visibility is left
    empty rather than understated;
  - impossible to score (imported history has no answer text): sentiment is
    left out and the other three weights are scaled up to fill 100%.

"Named" means the brand/domain appears in the answer text (mentions.mentioned).
Imported sheet history never recorded that, so for those rows "cited in the
answer" stands in for it and the metric row is flagged estimated=True.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from geo.models import Answer, Competitor, DailyCompetitorMetric, DailyMetric

IST = timezone(timedelta(hours=5, minutes=30))
OURS = ("own", "own brand")
WEIGHTS = {"mention": 0.40, "position": 0.25, "sov": 0.20, "sentiment": 0.15}


def ist_day(dt):
    if dt.tzinfo is None:          # SQLite hands back naive UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).date()


def _named(m):
    """(named?, estimated?) — falls back to 'cited' for imported history."""
    if m.mentioned is None:
        return bool(m.cited), True
    return bool(m.mentioned), False


def answer_facts(answer, relationships):
    """Reduce one answer + its mention rows to the facts the metrics need.
    relationships: {competitor_id: 'direct' | 'indirect' | ...}"""
    us_named = us_cited = estimated = False
    positions = []
    direct_named = 0
    competitors = {}
    for m in answer.mentions:
        named, est = _named(m)
        estimated |= est
        if m.entity_type in OURS:
            us_named |= named
            us_cited |= bool(m.cited)
            if m.score is not None:
                positions.append(m.score)
        elif m.competitor_id is not None:
            c = competitors.setdefault(m.competitor_id, {"named": False, "cited": False, "score": None})
            c["named"] |= named
            c["cited"] |= bool(m.cited)
            if m.score is not None:
                c["score"] = max(c["score"] or 0, m.score)
    for cid, c in competitors.items():
        if c["named"] and relationships.get(cid) == "direct":
            direct_named += 1
    return {
        "us_named": us_named,
        "us_cited": us_cited,
        "position": max(positions) if positions else None,
        "direct_named": direct_named,
        "sentiment": answer.sentiment,
        "has_text": bool(answer.answer_text),
        "estimated": estimated,
        "competitors": competitors,
    }


def _pct(n, d):
    return 100.0 * n / d if d else None


def visibility_score(mention_rate, position_score, share_of_voice, sentiment, sentiment_pending,
                     sentiment_unknowable=False):
    if sentiment_pending:
        return None
    parts = {
        "mention": mention_rate or 0.0,
        "position": position_score or 0.0,
        "sov": share_of_voice or 0.0,
        "sentiment": sentiment or 0.0,
    }
    weights = dict(WEIGHTS)
    if sentiment is None and sentiment_unknowable:
        del weights["sentiment"]
    total = sum(weights.values())
    return round(sum(w * parts[k] for k, w in weights.items()) / total, 2)


def summarize(facts):
    """Counts + derived metrics for one group of answer facts. Pure."""
    n = len(facts)
    us = sum(f["us_named"] for f in facts)
    us_or_direct = sum(1 for f in facts if f["us_named"] or f["direct_named"])
    direct_mentions = sum(f["direct_named"] for f in facts)
    cited = sum(f["us_cited"] for f in facts)
    pos = [f["position"] for f in facts if f["position"] is not None]
    sent = [f["sentiment"] for f in facts if f["us_named"] and f["sentiment"] is not None]
    pending = sum(1 for f in facts if f["us_named"] and f["sentiment"] is None and f["has_text"])
    unknowable = sum(1 for f in facts if f["us_named"] and f["sentiment"] is None and not f["has_text"])

    row = {
        "answers": n,
        "answers_naming_us": us,
        "answers_naming_us_or_direct": us_or_direct,
        "direct_competitor_mentions": direct_mentions,
        "answers_citing_us": cited,
        "position_sum": float(sum(pos)),
        "position_n": len(pos),
        "sentiment_sum": float(sum(sent)),
        "sentiment_n": len(sent),
        "sentiment_pending": pending,
        "sentiment_unknowable": unknowable,
        "mention_rate": _pct(us, us_or_direct),
        "share_of_voice": _pct(us, us + direct_mentions),
        "position_score": sum(pos) / len(pos) if pos else None,
        "citation_rate": _pct(cited, n),
        "covered": us > 0,
        "sentiment": sum(sent) / len(sent) if sent else None,
        "estimated": any(f["estimated"] for f in facts),
    }
    row["visibility_score"] = visibility_score(
        row["mention_rate"], row["position_score"], row["share_of_voice"], row["sentiment"], pending,
        sentiment_unknowable=unknowable > 0)
    return row


def summarize_competitors(facts):
    """{competitor_id: counts} for one group. Pure."""
    out = defaultdict(lambda: {"answers": 0, "mentions": 0, "cited": 0, "position_sum": 0.0, "position_n": 0})
    n = len(facts)
    for f in facts:
        for cid, c in f["competitors"].items():
            r = out[cid]
            r["mentions"] += int(c["named"])
            r["cited"] += int(c["cited"])
            if c["score"] is not None:
                r["position_sum"] += c["score"]
                r["position_n"] += 1
    for r in out.values():
        r["answers"] = n
    return dict(out)


def compute(session, days=7, today=None):
    """Recompute the last `days` IST days (inclusive of today). Idempotent:
    rows in the window are deleted and rebuilt, so late answers and re-runs
    are always reflected. Returns counts."""
    today = today or datetime.now(IST).date()
    start_day = today - timedelta(days=days - 1)
    since = datetime.combine(start_day, time.min, tzinfo=IST).astimezone(timezone.utc)

    relationships = {c.id: c.relationship_ for c in session.scalars(select(Competitor))}
    answers = session.scalars(
        select(Answer).options(selectinload(Answer.mentions))
        .where(Answer.created_at >= since, Answer.error.is_(None))).all()

    groups = defaultdict(list)
    for a in answers:
        day = ist_day(a.created_at)
        if day < start_day:
            continue
        groups[(day, a.client_id, a.question_id, a.ai_app)].append(answer_facts(a, relationships))

    session.execute(delete(DailyMetric).where(DailyMetric.day >= start_day))
    session.execute(delete(DailyCompetitorMetric).where(DailyCompetitorMetric.day >= start_day))

    now = datetime.now(timezone.utc)
    comp_rows = 0
    for (day, client_id, question_id, app), facts in groups.items():
        key = {"day": day, "client_id": client_id, "question_id": question_id, "ai_app": app}
        session.add(DailyMetric(**key, **summarize(facts), computed_at=now))
        for cid, counts in summarize_competitors(facts).items():
            session.add(DailyCompetitorMetric(**key, competitor_id=cid, **counts, computed_at=now))
            comp_rows += 1
    session.flush()
    return {"from": start_day.isoformat(), "to": today.isoformat(), "answers": len(answers),
            "metric_rows": len(groups), "competitor_rows": comp_rows}


def run(days=None, settings=None):
    """Entry point for the cron/CLI: score pending sentiment, then roll up."""
    from geo.config import get_settings
    from geo.db import session_scope
    from geo import sentiment

    settings = settings or get_settings()
    if not settings.database_url:
        return {"ok": False, "error": "DATABASE_URL not set."}
    days = days or settings.metrics_days
    today = datetime.now(IST).date()
    since = datetime.combine(today - timedelta(days=days - 1), time.min, tzinfo=IST).astimezone(timezone.utc)

    with session_scope(settings.database_url) as s:
        sent = sentiment.score_pending(s, settings, since)
    with session_scope(settings.database_url) as s:
        stats = compute(s, days=days, today=today)
    return {"ok": True, "sentiment": sent, **stats}
