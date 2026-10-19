"""
Runs split into small jobs, so no single serverless call has to do the whole
audit (one AI answer can take 20-60 s; a client with 12 questions on 2 apps
is 24 of them):

  plan()                         → clients that have active questions
  start(client_id)               → opens a run, returns its (question × AI app × repeat) tasks
  task(run_id, question_id, app, repeat)
                                 → asks ONE AI app ONE question and saves the answer;
                                   safe to retry (an existing answer is returned as-is)
  finish(run_id)                 → closes the run, writes the Results tabs (dual-write),
                                   and optionally rescores sentiment + rebuilds metrics

The web app drives these: the admin "Run now" button (from the browser, a
few tasks at a time, with progress) and the nightly Vercel cron (server-side).
Exposed over HTTP by api/manual_run.py, protected by CRON_SECRET.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from geo import audit, sheets_io
from geo.config import get_settings
from geo.db import session_scope
from geo.models import Answer, Client, Competitor, Question, Run
from geo.providers import ProviderResult, available_providers

IST = timezone(timedelta(hours=5, minutes=30))


class JobError(Exception):
    pass


def _apps_for(question, providers):
    return [a for a in (question.ai_apps or []) if a in providers] or list(providers)


def plan(settings=None):
    """Clients with at least one active question, after syncing the sheet."""
    settings = settings or get_settings()
    synced = _sync_sheet(settings)
    with session_scope(settings.database_url) as s:
        rows = s.execute(
            select(Client.id, Client.name, func.count(Question.id))
            .join(Question, Question.client_id == Client.id)
            .where(Question.active.is_(True))
            .group_by(Client.id, Client.name).order_by(Client.name)).all()
    return {"ok": True, "sheet": synced,
            "clients": [{"id": cid, "name": name, "active_questions": n} for cid, name, n in rows]}


def _sync_sheet(settings):
    """Pull sheet settings first so sheet edits count. A sheet problem never
    blocks a run — platform questions still work."""
    if not settings.google_sheet_id:
        return "not configured"
    try:
        r = audit.sync_only(settings)
        return "synced" if r.get("ok") else f"skipped: {r.get('error')}"
    except Exception as e:
        return f"skipped: {type(e).__name__}: {e}"


def start(client_id, trigger="manual", sync=True, settings=None):
    settings = settings or get_settings()
    providers = available_providers(settings)
    if not providers:
        raise JobError("No AI app API key configured (ANTHROPIC_API_KEY / OPENAI_API_KEY).")
    synced = _sync_sheet(settings) if sync else "not requested"

    run_id = str(uuid.uuid4())
    with session_scope(settings.database_url) as s:
        client = s.get(Client, client_id)
        if client is None:
            raise JobError(f"No client with id {client_id}.")
        questions = s.scalars(select(Question).where(
            Question.client_id == client_id, Question.active.is_(True)).order_by(Question.id)).all()
        tasks = [{"question_id": q.id, "question": q.text, "app": app, "repeat": r}
                 for q in questions for app in _apps_for(q, providers)
                 for r in range(1, settings.repeats_per_run + 1)]
        if tasks:
            s.add(Run(id=run_id, trigger=trigger, status="running",
                      started_at=datetime.now(timezone.utc),
                      stats={"client_id": client_id, "tasks": len(tasks)}))
        name = client.name
    return {"ok": True, "run_id": run_id if tasks else None, "client": {"id": client_id, "name": name},
            "sheet": synced, "tasks": tasks,
            "message": None if tasks else "This client has no active questions."}


def _check_and_context(s, question, client, providers):
    comps = s.scalars(select(Competitor).where(Competitor.client_id == client.id)).all()
    ctx = audit._client_context(client, comps, [], {(question.kind, question.text): question.id})
    check = {
        "kind": question.kind, "project": client.name, "domain": client.domain,
        "brand": client.brand or "",
        "competitors": [c["domain"] for c in ctx["competitors"] if c["domain"]],
        "text": question.text, "topic": question.topic, "funnel_stage": question.funnel_stage,
        "tags": list(question.tags or []), "platforms": _apps_for(question, providers),
    }
    return check, ctx


def _summary(answer):
    ours = [m for m in answer.mentions if m.entity_type in ("own", "own brand")]
    positions = [m.mention_position for m in ours if m.mention_position]
    return {
        "answer_id": answer.id,
        "error": answer.error,
        "named": any(bool(m.mentioned) for m in ours),
        "mention_position": min(positions) if positions else None,
        "cited": any(m.cited for m in ours),
    }


def task(run_id, question_id, app, repeat=1, settings=None):
    settings = settings or get_settings()
    providers = available_providers(settings)
    if app not in providers:
        raise JobError(f"AI app {app!r} is not configured.")

    # 1) read what we need, then let go of the DB connection during the AI call
    with session_scope(settings.database_url) as s:
        run = s.get(Run, run_id)
        if run is None:
            raise JobError("Unknown run.")
        existing = s.scalar(select(Answer).options(selectinload(Answer.mentions)).where(
            Answer.run_id == run_id, Answer.question_id == question_id,
            Answer.ai_app == app, Answer.repeat == repeat))
        if existing is not None and existing.error is None:
            return {"ok": True, "reused": True, **_summary(existing)}
        if existing is not None:      # a failed earlier attempt — replace it on retry
            s.delete(existing)
        question = s.get(Question, question_id)
        if question is None:
            raise JobError("Unknown question.")
        client = s.get(Client, question.client_id)
        check, ctx = _check_and_context(s, question, client, providers)

    # 2) ask the AI app (+ cheap competitor-discovery pass)
    try:
        rec = audit._ask(check, app, providers[app], repeat, settings)
    except Exception as e:
        rec = {"check": check, "platform": app, "repeat": repeat,
               "result": ProviderResult(error=f"{type(e).__name__}: {e}"),
               "brands": None, "extraction_error": ""}
    rec = audit.finish_record(rec, ctx)

    # 3) save it
    now = datetime.now(timezone.utc)
    with session_scope(settings.database_url) as s:
        new = audit.persist(s, run_id, [rec], {client.name: ctx}, now)
        answer = s.scalar(select(Answer).options(selectinload(Answer.mentions)).where(Answer.id == rec["answer_id"]))
        out = _summary(answer)
    return {"ok": True, "reused": False, "new_competitor_candidates": new, **out}


def _sheet_rows_for_run(s, run_id):
    """Rebuild Results-tab rows (same columns as the batch path) from the DB."""
    answers = s.scalars(select(Answer).options(selectinload(Answer.mentions))
                        .where(Answer.run_id == run_id).order_by(Answer.created_at)).all()
    clients = {c.id: c for c in s.scalars(select(Client))}
    kinds = {q.id: q.kind for q in s.scalars(select(Question).where(
        Question.id.in_({a.question_id for a in answers})))} if answers else {}
    by_app = {}
    for a in answers:
        ts = a.created_at if a.created_at.tzinfo else a.created_at.replace(tzinfo=timezone.utc)
        ist = ts.astimezone(IST)
        common = dict(run_id=run_id, answer_id=a.id, ai_app=a.ai_app, model=a.model,
                      prompt_type=kinds.get(a.question_id, "question"), repeat=a.repeat)
        client = clients.get(a.client_id)
        base = (ist.strftime("%Y-%m-%d"), ist.strftime("%H:%M"), client.name if client else "",
                a.question_text, a.intent or "")
        rows = by_app.setdefault(a.ai_app, [])
        if a.error:
            rows.append(sheets_io.results_row(*base, "own", client.domain if client else "", None, None, False, "", False,
                                              a.error, None, **common))
            continue
        order = {"own": 0, "own brand": 1, "competitor": 2}
        for m in sorted(a.mentions, key=lambda m: (order.get(m.entity_type, 3), m.id or 0)):
            rows.append(sheets_io.results_row(
                *base, m.entity_type, m.entity_name, m.rank, m.score, m.cited, a.top_result or "",
                a.grounded, "", m.answer_position, mentioned=bool(m.mentioned),
                mention_position=m.mention_position, **common))
    return by_app


def finish(run_id, metrics=True, settings=None):
    settings = settings or get_settings()
    with session_scope(settings.database_url) as s:
        run = s.get(Run, run_id)
        if run is None:
            raise JobError("Unknown run.")
        total = s.scalar(select(func.count()).select_from(Answer).where(Answer.run_id == run_id)) or 0
        errors = s.scalar(select(func.count()).select_from(Answer).where(
            Answer.run_id == run_id, Answer.error.is_not(None))) or 0
        new_comps = s.scalar(select(func.count()).select_from(Competitor).where(
            Competitor.relationship_ == "pending",
            Competitor.client_id == (run.stats or {}).get("client_id"))) or 0
        expected = (run.stats or {}).get("tasks", total)
        run.status = "done" if total >= expected and errors == 0 else ("partial" if total else "failed")
        run.finished_at = datetime.now(timezone.utc)
        run.stats = {**(run.stats or {}), "answers": total, "errors": errors,
                     "pending_competitors": new_comps}
        stats = dict(run.stats)
        status = run.status
        sheet_rows = _sheet_rows_for_run(s, run_id) if settings.sheets_dual_write else {}

    sheet = "off"
    if sheet_rows and settings.google_sheet_id:
        try:
            svc = sheets_io.sheets_service(settings.google_service_account_json)
            providers = available_providers(settings)
            for app, rows in sheet_rows.items():
                tab = providers.get(app, {}).get("tab", f"Results - {app}")
                sheets_io.ensure_results_header(svc, settings.google_sheet_id, tab)
                sheets_io.append_results(svc, settings.google_sheet_id, tab, rows)
            sheet = "written"
        except Exception as e:
            sheet = f"failed: {type(e).__name__}: {e}"

    out = {"ok": True, "run_id": run_id, "status": status, "stats": stats, "sheet": sheet}
    if metrics:
        from geo import metrics as metrics_mod
        out["metrics"] = metrics_mod.run(settings=settings)
    return out
