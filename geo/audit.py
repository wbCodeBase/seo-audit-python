"""
One audit run:

  1. Read active checks (keywords + questions) from the Google Sheet.
  2. If DATABASE_URL is set: sync clients / questions / sheet competitors
     into Postgres and open a `runs` row. A DB failure here doesn't stop the
     run — it continues sheet-only and reports db_error.
  3. Ask every enabled AI app every check, REPEATS_PER_RUN times.
  4. Score each tracked entity (own domain, own brand, competitors — sheet
     ones plus discovered ones a human marked direct/indirect), detect text
     mentions, and discover new candidate competitors.
  5. Save answers / mentions / citations / candidates to Postgres, and write
     the Results tabs if SHEETS_DUAL_WRITE is on (always, if there's no DB).
"""
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from geo import discovery, sheets_io
from geo.config import get_settings
from geo.matching import clean_domain, domain_stem, entity_aliases, find_entity, mention_positions
from geo.models import Answer, Citation, Client, Competitor, Mention, Question, Run
from geo.providers import available_providers
from geo.scoring import classify_intent, visibility_score

IST = timezone(timedelta(hours=5, minutes=30))
TRACKED_RELATIONSHIPS = ("direct", "indirect")


# ── Config sync (sheet → DB) ────────────────────────────────────────────────

def _project_context(check):
    """Tracking context for a project when there's no database."""
    return {
        "client_id":     None,
        "question_ids":  {},
        "self_aliases":  [],
        "competitors":   [{"key": clean_domain(c), "name": c, "domain": c, "id": None}
                          for c in check["competitors"]],
    }


def sync_config(session, checks, now):
    """Upsert clients, questions and sheet-listed competitors. Returns
    {project: context} with DB ids and every competitor to track."""
    by_project = {}
    for ch in checks:
        by_project.setdefault(ch["project"], []).append(ch)

    contexts = {}
    for project, project_checks in by_project.items():
        first = project_checks[0]
        client = session.scalar(select(Client).where(Client.name == project))
        if client is None:
            client = Client(name=project, domain=first["domain"], brand=first["brand"] or None, aliases=[])
            session.add(client)
            session.flush()
        else:
            client.domain = first["domain"]
            if first["brand"]:
                client.brand = first["brand"]

        existing_q = {(q.kind, q.text): q for q in session.scalars(
            select(Question).where(Question.client_id == client.id))}
        question_ids, seen = {}, set()
        for ch in project_checks:
            k = (ch["kind"], ch["text"])
            q = existing_q.get(k)
            if q is None:
                q = Question(client_id=client.id, kind=ch["kind"], text=ch["text"])
                session.add(q)
                existing_q[k] = q
            q.topic, q.funnel_stage, q.tags = ch["topic"], ch["funnel_stage"], ch["tags"]
            q.ai_apps, q.active = ch["platforms"], True
            seen.add(k)
        # A sheet question whose row is gone or inactive is paused. Questions
        # created in the web app (source='platform') are never touched here.
        for k, q in existing_q.items():
            if k not in seen and q.source == "sheet":
                q.active = False
        session.flush()
        for k in seen:
            question_ids[k] = existing_q[k].id

        comps = {c.key: c for c in session.scalars(
            select(Competitor).where(Competitor.client_id == client.id))}
        sheet_keys = []
        for ch in project_checks:
            for dom in ch["competitors"]:
                key = clean_domain(dom)
                if not key or key in sheet_keys:
                    continue
                sheet_keys.append(key)
                c = comps.get(key)
                if c is None:
                    c = Competitor(client_id=client.id, key=key, name=dom, domain=key,
                                   relationship_="direct", source="sheet", times_seen=0)
                    session.add(c)
                    comps[key] = c
                elif c.relationship_ == "pending":
                    c.relationship_ = "direct"
        session.flush()
        contexts[project] = _client_context(client, comps.values(), sheet_keys, question_ids)
    return contexts


def _client_context(client, competitors, sheet_keys, question_ids):
    """Sheet-listed competitors are always tracked (in sheet order); any
    other competitor is tracked once someone marks it direct/indirect."""
    comps = {c.key: c for c in competitors}
    tracked = [{"key": k, "name": comps[k].name, "domain": comps[k].domain, "id": comps[k].id}
               for k in sheet_keys]
    for c in sorted(comps.values(), key=lambda c: c.id or 0):
        if c.key not in sheet_keys and c.relationship_ in TRACKED_RELATIONSHIPS:
            tracked.append({"key": c.key, "name": c.name, "domain": c.domain, "id": c.id})
    return {
        "client_id":    client.id,
        "question_ids": question_ids,
        "self_aliases": list(client.aliases or []) +
                        [c.name for c in comps.values() if c.relationship_ == "self"],
        "competitors":  tracked,
    }


def load_platform_checks(session, providers, contexts):
    """Active questions created in the web app. Adds their clients to
    `contexts` (in place) and returns them as checks shaped like sheet rows."""
    checks = []
    rows = session.execute(
        select(Question, Client).join(Client, Question.client_id == Client.id)
        .where(Question.source == "platform", Question.active.is_(True))
        .order_by(Question.id)).all()
    for q, client in rows:
        ctx = contexts.get(client.name)
        if ctx is None:
            comps = session.scalars(select(Competitor).where(Competitor.client_id == client.id)).all()
            ctx = contexts[client.name] = _client_context(client, comps, [], {})
        ctx["question_ids"][(q.kind, q.text)] = q.id
        apps = [a for a in (q.ai_apps or []) if a in providers] or list(providers)
        checks.append({
            "kind":         q.kind,
            "project":      client.name,
            "domain":       client.domain,
            "brand":        client.brand or "",
            "competitors":  [c["domain"] for c in ctx["competitors"] if c["domain"]],
            "text":         q.text,
            "topic":        q.topic,
            "funnel_stage": q.funnel_stage,
            "tags":         list(q.tags or []),
            "platforms":    apps,
        })
    return checks


# ── Scoring one answer ──────────────────────────────────────────────────────

def evaluate(check, ctx, result):
    """Pure: turns one ProviderResult into entity rows. Returns a list of
    dicts, one per tracked entity (just the 'own' row on error)."""
    parsed = result.parsed
    if result.error:
        return [{"entity_type": "own", "entity_name": check["domain"], "competitor_key": None,
                 "rank": None, "score": None, "cited": False, "answer_position": None,
                 "mentioned": False, "mention_position": None}]

    grounded = parsed.grounded
    entities = [("own", check["domain"], None,
                 find_entity(parsed.rankings, domain=check["domain"]),
                 entity_aliases(domain=check["domain"]))]
    if check["brand"]:
        entities.append(("own brand", check["brand"], None,
                         find_entity(parsed.rankings, brand=check["brand"]),
                         entity_aliases(name=check["brand"], extra=ctx["self_aliases"])))
    for comp in ctx["competitors"]:
        if comp["domain"]:
            item = find_entity(parsed.rankings, domain=comp["domain"])
        else:
            item = find_entity(parsed.rankings, brand=comp["name"])
        label = comp["domain"] or comp["name"]
        name = comp["name"] if comp["name"] != comp["domain"] else None
        entities.append(("competitor", label, comp["key"], item,
                         entity_aliases(name=name, domain=comp["domain"])))

    alias_map = {i: e[4] for i, e in enumerate(entities)}
    groups = {i: ("self" if e[0] in ("own", "own brand") else e[2] or i) for i, e in enumerate(entities)}
    mentions = mention_positions(parsed.answer_text, alias_map, groups)

    rows = []
    for i, (etype, name, comp_key, item, _aliases) in enumerate(entities):
        rank = item["rank"] if item else None
        cited = bool(item and item.get("cited"))
        rows.append({
            "entity_type":      etype,
            "entity_name":      name,
            "competitor_key":   comp_key,
            "rank":             rank,
            "score":            visibility_score(rank, grounded, cited),
            "cited":            cited,
            "answer_position":  item.get("answer_position") if item else None,
            "mentioned":        mentions[i]["mentioned"],
            "mention_position": mentions[i]["position"],
        })
    return rows


# ── Running the checks ──────────────────────────────────────────────────────

def _ask(check, platform_key, provider, repeat, settings):
    result = provider["ask"](check["text"])
    brands, extraction_error = None, ""
    if settings.discovery_enabled and settings.anthropic_api_key and not result.error:
        brands, info = discovery.extract_brands_llm(
            result.parsed.answer_text, check["text"], settings.anthropic_api_key, settings.discovery_model,
            brand=check["brand"], domain=check["domain"])
        if brands is None:
            extraction_error = info
        elif info:
            result.usage["discovery"] = info
    return {"check": check, "platform": platform_key, "repeat": repeat,
            "result": result, "brands": brands, "extraction_error": extraction_error}


def run_checks(checks, providers, settings, contexts):
    tasks = [(ch, p, r) for ch in checks for p in ch["platforms"] if p in providers
             for r in range(1, settings.repeats_per_run + 1)]
    records = []
    if not tasks:
        return records
    with ThreadPoolExecutor(max_workers=min(settings.max_workers, len(tasks))) as pool:
        futures = {pool.submit(_ask, ch, p, providers[p], r, settings): (ch, p, r) for ch, p, r in tasks}
        for fut in as_completed(futures):
            ch, p, r = futures[fut]
            try:
                rec = fut.result()
            except Exception as e:  # provider code already catches API errors; this is a last resort
                from geo.providers import ProviderResult
                rec = {"check": ch, "platform": p, "repeat": r,
                       "result": ProviderResult(error=f"{type(e).__name__}: {e}"),
                       "brands": None, "extraction_error": ""}
            records.append(finish_record(rec, contexts[ch["project"]]))
    return records


def finish_record(rec, ctx):
    """Score one asked check: entity rows, #1 result, new-competitor candidates."""
    ch = rec["check"]
    rec["answer_id"] = str(uuid.uuid4())
    rec["intent"] = classify_intent(ch["text"])
    rec["entities"] = evaluate(ch, ctx, rec["result"])
    parsed = rec["result"].parsed
    rec["top1"] = parsed.rankings[0]["domain"] if parsed.rankings else ""
    if rec["result"].error:
        rec["candidates"] = []
    else:
        known = discovery.KnownEntities()
        known.add(name=ch["brand"], domain=ch["domain"])
        for a in ctx["self_aliases"]:
            known.add(name=a)
        for c in ch["competitors"]:
            known.add(domain=c)
        rec["candidates"] = discovery.discover(rec["brands"], parsed.citations, known)
    return rec


# ── Persisting ──────────────────────────────────────────────────────────────

def persist(session, run_id, records, contexts, now):
    """Write answers, mentions, citations and discovered competitors.
    Returns the number of new candidate competitors flagged for review."""
    comp_cache = {}

    def competitor(client_id, key):
        if (client_id, key) not in comp_cache:
            comp_cache[(client_id, key)] = session.scalar(
                select(Competitor).where(Competitor.client_id == client_id, Competitor.key == key))
        return comp_cache[(client_id, key)]

    new_candidates = 0
    for rec in records:
        ch, ctx, res = rec["check"], contexts[rec["check"]["project"]], rec["result"]
        client_id = ctx["client_id"]
        question_id = ctx["question_ids"][(ch["kind"], ch["text"])]
        usage = dict(res.usage)
        if rec.get("extraction_error"):
            usage["discovery_error"] = rec["extraction_error"]

        answer = Answer(
            id=rec["answer_id"], run_id=run_id, question_id=question_id, client_id=client_id,
            ai_app=rec["platform"], model=res.model or None, repeat=rec["repeat"],
            question_text=ch["text"], intent=rec["intent"],
            answer_text=res.parsed.answer_text or None, grounded=res.parsed.grounded,
            error=res.error or None, top_result=rec["top1"] or None, usage=usage, created_at=now,
        )
        session.add(answer)

        if not res.error:
            for e in rec["entities"]:
                comp = competitor(client_id, e["competitor_key"]) if e["competitor_key"] else None
                answer.mentions.append(Mention(
                    client_id=client_id, competitor_id=comp.id if comp else None, entity_type=e["entity_type"],
                    entity_name=e["entity_name"], mentioned=e["mentioned"],
                    mention_position=e["mention_position"], rank=e["rank"], score=e["score"],
                    cited=e["cited"], answer_position=e["answer_position"],
                ))
            for c in res.parsed.citations:
                answer.citations.append(Citation(
                    client_id=client_id, position=c["position"], url=c["url"], domain=c["domain"], title=c["title"] or None))

        for cand in rec["candidates"]:
            comp = competitor(client_id, cand["key"]) or _same_competitor(session, client_id, cand)
            if comp is None:
                comp = Competitor(
                    client_id=client_id, key=cand["key"], name=cand["name"][:200],
                    domain=cand["domain"], relationship_="pending", source="discovered",
                    times_seen=0, first_seen_at=now, example_question_id=question_id,
                )
                session.add(comp)
                comp_cache[(client_id, cand["key"])] = comp
                new_candidates += 1
            comp.times_seen = (comp.times_seen or 0) + 1
            comp.last_seen_at = now
        session.flush()
    return new_candidates


def _same_competitor(session, client_id, cand):
    """An existing competitor that is the same business under a different key
    — e.g. first seen with its website ('sarctechnology.com'), later named
    without one ('SARC Technology') — matched on normalised name or on the
    domain's brand label."""
    want = discovery._compact(cand["name"])
    want_stem = domain_stem(cand["domain"]) if cand.get("domain") else ""
    for c in session.scalars(select(Competitor).where(Competitor.client_id == client_id)):
        names = {discovery._compact(c.name)}
        if c.domain:
            names.add(discovery._compact(domain_stem(c.domain)))
        if (want and want in names) or (want_stem and len(want_stem) >= 4 and want_stem in names):
            return c
    return None


# ── Sheet rows ──────────────────────────────────────────────────────────────

def sheet_rows(rec, run_id, date_s, time_s):
    ch, res = rec["check"], rec["result"]
    rows = []
    for e in rec["entities"]:
        rows.append(sheets_io.results_row(
            date_s, time_s, ch["project"], ch["text"], rec["intent"], e["entity_type"],
            e["entity_name"], e["rank"], e["score"], e["cited"], rec["top1"],
            res.parsed.grounded and not res.error, res.error, e["answer_position"],
            run_id=run_id, answer_id=rec["answer_id"], ai_app=rec["platform"], model=res.model,
            prompt_type=ch["kind"], repeat=rec["repeat"], mentioned=e["mentioned"],
            mention_position=e["mention_position"],
        ))
    return rows


# ── Entry points ────────────────────────────────────────────────────────────

def sync_only(settings=None):
    """Copy the sheet's clients / questions / competitors into the database
    without asking any AI app (no API cost). Useful right after editing the
    sheet, so the web app reflects it before the next scheduled run."""
    from geo.db import session_scope

    settings = settings or get_settings()
    if not (settings.google_sheet_id and settings.database_url):
        return {"ok": False, "error": "GOOGLE_SHEET_ID and DATABASE_URL must both be set."}
    svc = sheets_io.sheets_service(settings.google_service_account_json)
    if not svc:
        return {"ok": False, "error": "GOOGLE_SERVICE_ACCOUNT_JSON not set or invalid."}
    known = {k: {} for k in ("claude", "chatgpt")}
    checks, skipped = sheets_io.read_checks(svc, settings.google_sheet_id, known)
    with session_scope(settings.database_url) as s:
        contexts = sync_config(s, checks, datetime.now(timezone.utc))
    return {"ok": True, "clients": sorted(contexts), "active_questions": len(checks), "skipped": skipped}


def run(trigger="cron", settings=None):
    from geo.db import session_scope

    settings = settings or get_settings()
    if not settings.google_sheet_id:
        return {"ok": False, "error": "GOOGLE_SHEET_ID not set."}
    providers = available_providers(settings)
    if not providers:
        return {"ok": False, "error": "No platform API key configured. Set ANTHROPIC_API_KEY and/or OPENAI_API_KEY."}
    svc = sheets_io.sheets_service(settings.google_service_account_json)
    if not svc:
        return {"ok": False, "error": "GOOGLE_SERVICE_ACCOUNT_JSON not set or invalid."}

    checks, skipped = sheets_io.read_checks(svc, settings.google_sheet_id, providers)

    now = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    use_db, db_error = bool(settings.database_url), ""

    contexts = {}
    if use_db:
        try:
            with session_scope(settings.database_url) as s:
                contexts = sync_config(s, checks, now)
                platform_checks = load_platform_checks(s, providers, contexts)
                if checks or platform_checks:
                    s.add(Run(id=run_id, trigger=trigger, status="running", started_at=now))
            checks = checks + platform_checks
        except Exception as e:
            use_db, db_error = False, f"{type(e).__name__}: {e}"
    if not checks:
        out = {"ok": True, "message": "No active questions found (sheet or platform).",
               "rows_written": {}, "skipped": skipped}
        if db_error:
            out["db_error"] = db_error
        return out
    if not use_db:
        contexts = {}
        for ch in checks:
            contexts.setdefault(ch["project"], _project_context(ch))

    records = run_checks(checks, providers, settings, contexts)
    errors = [{"keyword": r["check"]["text"], "platform": r["platform"], "error": r["result"].error}
              for r in records if r["result"].error]

    rows_written = {}
    write_sheet = settings.sheets_dual_write or not use_db
    if write_sheet:
        now_ist = now.astimezone(IST)
        date_s, time_s = now_ist.strftime("%Y-%m-%d"), now_ist.strftime("%H:%M")
        out_rows = {p: [] for p in providers}
        for rec in records:
            out_rows[rec["platform"]] += sheet_rows(rec, run_id, date_s, time_s)
        for p, cfg in providers.items():
            sheets_io.ensure_results_header(svc, settings.google_sheet_id, cfg["tab"])
            sheets_io.append_results(svc, settings.google_sheet_id, cfg["tab"], out_rows[p])
            rows_written[p] = len(out_rows[p])

    new_candidates = 0
    if use_db:
        stats = {"answers": len(records), "errors": len(errors), "skipped": len(skipped)}
        try:
            with session_scope(settings.database_url) as s:
                new_candidates = persist(s, run_id, records, contexts, now)
                stats["new_competitor_candidates"] = new_candidates
                r = s.get(Run, run_id)
                r.status, r.finished_at, r.stats = "done", datetime.now(timezone.utc), stats
        except Exception as e:
            db_error = f"{type(e).__name__}: {e}"
            try:
                with session_scope(settings.database_url) as s:
                    r = s.get(Run, run_id)
                    if r:
                        r.status, r.finished_at = "failed", datetime.now(timezone.utc)
                        r.stats = {**stats, "error": db_error[:1000]}
            except Exception:
                pass

    out = {
        "ok":                 not (db_error and not write_sheet),
        "run_id":             run_id,
        "platforms_run":      list(providers.keys()),
        "keywords_processed": len(checks),
        "answers":            len(records),
        "rows_written":       rows_written,
        "saved_to_database":  use_db and not db_error,
        "new_competitor_candidates": new_candidates,
        "skipped":            skipped,
        "errors":             errors,
    }
    if db_error:
        out["db_error"] = db_error
    return out
