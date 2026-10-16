"""
One-off import of existing "Results - <App>" sheet history into Postgres.

Handles every header version the tabs have had (12 columns, + Error,
+ Answer Position, + the Phase 1 columns) by reading columns by header
name. Rows are grouped back into answers — one per (date, time, project,
keyword, app) — and each distinct (date, time) becomes one run with
trigger='import'.

Idempotent: run ids are derived from the timestamp (uuid5), so re-running
skips runs already imported. Rows that already carry a Run ID were written
by the new pipeline, which saved them to the DB itself, so they're skipped.

What history can't tell us is left NULL rather than guessed: answer text,
model, cited sources, and whether a brand was *named* in the text
(mentions.mentioned).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from geo.matching import clean_domain
from geo.models import Answer, Client, Competitor, Mention, Question, Run

IST = timezone(timedelta(hours=5, minutes=30))
_NS = uuid.UUID("5b0f3c2e-8f3a-4f61-9a43-6f1f2e9d7a10")

APP_TABS = {"claude": "Results - Claude", "chatgpt": "Results - ChatGPT"}


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _yes(v):
    return (v or "").strip().lower() == "yes"


def _parse_ts(date_s, time_s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(f"{date_s.strip()} {time_s.strip()}", fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def run_id_for(date_s, time_s):
    return str(uuid.uuid5(_NS, f"import|{date_s.strip()}|{time_s.strip()}"))


def group_history(values, ai_app):
    """values: the tab's rows including the header row. Returns a list of
    answer dicts: {run_id, ts, project, keyword, ai_app, intent, grounded,
    error, top_result, entities:[...]}. Pure — no DB, no Sheets API."""
    if not values:
        return []
    header = [h.strip() for h in values[0]]
    col = {h: i for i, h in enumerate(header)}

    def get(row, name):
        i = col.get(name)
        return row[i] if i is not None and i < len(row) else ""

    answers, repeats = {}, {}
    for row in values[1:]:
        if not any((c or "").strip() for c in row):
            continue  # day separator
        if get(row, "Run ID").strip():
            continue  # written by the new pipeline, already in the DB
        date_s, time_s = get(row, "Date"), get(row, "Time (IST)")
        ts = _parse_ts(date_s, time_s)
        project, keyword = get(row, "Project").strip(), get(row, "Keyword").strip()
        if ts is None or not keyword:
            continue
        etype, ename = get(row, "Entity Type").strip(), get(row, "Entity Domain").strip()
        key = (date_s.strip(), time_s.strip(), project, keyword)
        repeat = repeats.get(key, 1)
        a = answers.get(key + (repeat,))
        # Two runs of the same keyword within the same minute share a
        # timestamp: an entity showing up twice means a new answer started.
        if a is not None and (etype, ename) in {(e["entity_type"], e["entity_name"]) for e in a["entities"]}:
            repeat = repeats[key] = repeat + 1
            a = None
        if a is None:
            a = answers[key + (repeat,)] = {
                "run_id":     run_id_for(date_s, time_s),
                "ts":         ts,
                "project":    project,
                "keyword":    keyword,
                "ai_app":     ai_app,
                "repeat":     repeat,
                "intent":     get(row, "Intent").strip() or None,
                "grounded":   _yes(get(row, "Grounded")),
                "error":      get(row, "Error").strip() or None,
                "top_result": get(row, "#1 Result").strip() or None,
                "entities":   [],
            }
        a["entities"].append({
            "entity_type":     etype,
            "entity_name":     ename,
            "rank":            _int(get(row, "Rank")),
            "score":           _int(get(row, "Score")),
            "cited":           _yes(get(row, "Cited In Answer")),
            "answer_position": _int(get(row, "Answer Position")),
        })
    return list(answers.values())


def import_answers(session, answers):
    """Write grouped answers. Returns {"runs": n, "answers": n, "skipped_runs": n}."""
    stats = {"runs": 0, "answers": 0, "skipped_runs": 0}
    existing_runs = set()
    new_runs = {}
    clients, questions, comps = {}, {}, {}

    def client_for(a):
        name = a["project"] or next((e["entity_name"] for e in a["entities"] if e["entity_type"] == "own"), "")
        if name not in clients:
            c = session.scalar(select(Client).where(Client.name == name))
            if c is None:
                own = next((e["entity_name"] for e in a["entities"] if e["entity_type"] == "own"), name)
                brand = next((e["entity_name"] for e in a["entities"] if e["entity_type"] == "own brand"), None)
                c = Client(name=name, domain=own, brand=brand, aliases=[])
                session.add(c)
                session.flush()
            clients[name] = c
        return clients[name]

    def question_for(client, text):
        k = (client.id, text)
        if k not in questions:
            q = session.scalar(select(Question).where(
                Question.client_id == client.id, Question.kind == "keyword", Question.text == text))
            if q is None:
                # Imported questions start inactive; the next live run
                # re-activates whatever is still active in the sheet.
                q = Question(client_id=client.id, kind="keyword", text=text, tags=[], ai_apps=[],
                             active=False, frequency="daily")
                session.add(q)
                session.flush()
            questions[k] = q
        return questions[k]

    def competitor_for(client, name):
        key = clean_domain(name)
        k = (client.id, key)
        if k not in comps:
            c = session.scalar(select(Competitor).where(Competitor.client_id == client.id, Competitor.key == key))
            if c is None:
                c = Competitor(client_id=client.id, key=key, name=name, domain=key,
                               relationship_="direct", source="sheet", times_seen=0)
                session.add(c)
                session.flush()
            comps[k] = c
        return comps[k]

    for a in answers:
        rid = a["run_id"]
        if rid in existing_runs:
            continue
        if rid not in new_runs:
            if session.get(Run, rid) is not None:
                existing_runs.add(rid)
                stats["skipped_runs"] += 1
                continue
            run = Run(id=rid, trigger="import", status="done", started_at=a["ts"],
                      finished_at=a["ts"], stats={})
            session.add(run)
            session.flush()  # answers reference runs.id; no ORM relationship orders this for us
            new_runs[rid] = run
            stats["runs"] += 1

        client = client_for(a)
        q = question_for(client, a["keyword"])
        ans = Answer(
            run_id=rid, question_id=q.id, client_id=client.id, ai_app=a["ai_app"], model=None,
            repeat=a["repeat"], question_text=a["keyword"], intent=a["intent"], answer_text=None,
            grounded=a["grounded"], error=a["error"], top_result=a["top_result"],
            usage={"imported": True}, created_at=a["ts"],
        )
        session.add(ans)
        if not a["error"]:
            for e in a["entities"]:
                comp = competitor_for(client, e["entity_name"]) if e["entity_type"] == "competitor" else None
                ans.mentions.append(Mention(
                    client_id=client.id, competitor_id=comp.id if comp else None, entity_type=e["entity_type"],
                    entity_name=e["entity_name"], mentioned=None, mention_position=None,
                    rank=e["rank"], score=e["score"], cited=e["cited"],
                    answer_position=e["answer_position"],
                ))
        stats["answers"] += 1
        new_runs[rid].stats = {"answers": new_runs[rid].stats.get("answers", 0) + 1}
    session.flush()
    return stats
