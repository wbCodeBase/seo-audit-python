"""
Google Sheets input (Keywords + Questions tabs) and the Results-tab
dual-write. Read/write helpers are unchanged from api/scheduled_audit.py
except where noted.

Keywords tab (row 1 header, data from row 2) — unchanged:
  A Project | B Domain | C Brand | D Competitors | E Keyword | F Active | G Platforms

Questions tab (new, optional — skipped if the tab doesn't exist):
  A Project | B Question | C Topic | D Funnel Stage (TOFU/MOFU/BOFU) |
  E Tags (comma-separated) | F Active | G Platforms
  A question's domain/brand/competitors come from the Keywords rows with the
  same Project, so a client is set up once and can have both.
"""
import base64
import json

RESULTS_HEADER = [
    "Date", "Time (IST)", "Project", "Keyword", "Intent",
    "Entity Type", "Entity Domain", "Rank", "Score",
    "Cited In Answer", "#1 Result", "Grounded", "Error", "Answer Position",
    # Phase 1 additions — appended so every existing column keeps its letter.
    "Run ID", "Answer ID", "AI App", "Model", "Prompt Type", "Repeat #",
    "Mentioned In Answer", "Mention Position",
]

ACTIVE_VALUES = {"", "true", "yes", "y", "1", "on", "active"}


def is_active(cell):
    """Blank or an explicit yes-value means active; anything else — FALSE,
    No, 0, off, paused, inactive, a typo — means skip. The old check only
    skipped the exact text FALSE, so 'No' or 'paused' still ran."""
    return (cell or "").strip().lower() in ACTIVE_VALUES


def _split(cell):
    return [x.strip() for x in (cell or "").split(",") if x.strip()]


def _platforms(cell, known):
    requested = [p.lower() for p in _split(cell)]
    return [p for p in requested if p in known] or list(known)


_FUNNEL_SYNONYMS = {
    "tofu": "TOFU", "awareness": "TOFU", "top": "TOFU",
    "mofu": "MOFU", "consideration": "MOFU", "middle": "MOFU",
    "bofu": "BOFU", "decision": "BOFU", "conversion": "BOFU", "bottom": "BOFU",
}


def _funnel(cell):
    """TOFU/MOFU/BOFU (or awareness/consideration/decision). Unrecognised
    text is kept as typed rather than silently dropped."""
    v = (cell or "").strip()
    if not v:
        return None
    return _FUNNEL_SYNONYMS.get(v.lower(), v)


def sheets_service(service_account_json):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    raw = (service_account_json or "").strip()
    if not raw:
        return None
    try:
        info = json.loads(raw) if raw.startswith("{") else json.loads(base64.b64decode(raw))
    except Exception:
        return None
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def qrange(tab, cell_range):
    """Always single-quote the tab name in A1 notation."""
    return f"'{tab}'!{cell_range}"


def _get_rows(svc, sheet_id, tab, cell_range):
    resp = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=qrange(tab, cell_range)
    ).execute()
    return resp.get("values", [])


def _tab_titles(svc, sheet_id):
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def parse_keyword_rows(rows, known_platforms):
    """Returns (checks, skipped). Pure function so it can be unit-tested
    without the Sheets API."""
    out, skipped = [], []
    for i, row in enumerate(rows, start=2):
        row = list(row) + [""] * (7 - len(row))
        project, domain, brand, competitors, keyword, active, platforms_cell = row[:7]
        domain, keyword = domain.strip(), keyword.strip()
        if not domain or not keyword:
            continue
        if not is_active(active):
            skipped.append({"tab": "Keywords", "row": i, "text": keyword, "active": active})
            continue
        out.append({
            "kind":         "keyword",
            "project":      project.strip() or domain,
            "domain":       domain,
            "brand":        brand.strip(),
            "competitors":  _split(competitors),
            "text":         keyword,
            "topic":        None,
            "funnel_stage": None,
            "tags":         [],
            "platforms":    _platforms(platforms_cell, known_platforms),
        })
    return out, skipped


def parse_question_rows(rows, known_platforms, projects):
    """projects: {project: {"domain","brand","competitors"}} built from the
    Keywords tab (all rows, active or not, so pausing every keyword of a
    client doesn't orphan its questions). Returns (checks, skipped)."""
    out, skipped = [], []
    for i, row in enumerate(rows, start=2):
        row = list(row) + [""] * (7 - len(row))
        project, question, topic, funnel, tags, active, platforms_cell = row[:7]
        project, question = project.strip(), question.strip()
        if not question:
            continue
        if not is_active(active):
            skipped.append({"tab": "Questions", "row": i, "text": question, "active": active})
            continue
        client = projects.get(project)
        if not client:
            skipped.append({"tab": "Questions", "row": i, "text": question,
                            "reason": f"Project '{project}' has no row in the Keywords tab"})
            continue
        out.append({
            "kind":         "question",
            "project":      project,
            "domain":       client["domain"],
            "brand":        client["brand"],
            "competitors":  list(client["competitors"]),
            "text":         question,
            "topic":        topic.strip() or None,
            "funnel_stage": _funnel(funnel),
            "tags":         _split(tags),
            "platforms":    _platforms(platforms_cell, known_platforms),
        })
    return out, skipped


def projects_from_keyword_rows(rows):
    projects = {}
    for row in rows:
        row = list(row) + [""] * (7 - len(row))
        project, domain, brand, competitors = (c.strip() for c in row[:4])
        if not domain:
            continue
        p = projects.setdefault(project or domain, {"domain": domain, "brand": brand, "competitors": []})
        if brand and not p["brand"]:
            p["brand"] = brand
        for c in _split(competitors):
            if c not in p["competitors"]:
                p["competitors"].append(c)
    return projects


def read_checks(svc, sheet_id, known_platforms):
    """Everything to audit this run, from both tabs. Returns (checks, skipped)."""
    kw_rows = _get_rows(svc, sheet_id, "Keywords", "A2:G")
    checks, skipped = parse_keyword_rows(kw_rows, known_platforms)
    if "Questions" in _tab_titles(svc, sheet_id):
        q_rows = _get_rows(svc, sheet_id, "Questions", "A2:G")
        q_checks, q_skipped = parse_question_rows(q_rows, known_platforms, projects_from_keyword_rows(kw_rows))
        checks += q_checks
        skipped += q_skipped
    return checks, skipped


def ensure_results_header(svc, sheet_id, tab):
    """Create the tab if missing, then (re)write row 1 to the current header —
    safe with data below, and lets old tabs pick up new columns."""
    if tab not in _tab_titles(svc, sheet_id):
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=qrange(tab, "A1"),
        valueInputOption="RAW", body={"values": [RESULTS_HEADER]},
    ).execute()


def _next_empty_row(svc, sheet_id, tab):
    """Row after the last non-empty cell in column A. Not using
    values.append(): its table detection stops at the first blank row, which
    is exactly the day separator we insert on purpose."""
    return len(_get_rows(svc, sheet_id, tab, "A:A")) + 1


def append_results(svc, sheet_id, tab, rows):
    """Write this run's rows at the true bottom, with one blank separator row
    before them if the tab already has data."""
    if not rows:
        return
    next_row = _next_empty_row(svc, sheet_id, tab)
    has_prior_data = next_row > 2
    blank_row = [""] * len(RESULTS_HEADER)
    payload = ([blank_row] + rows) if has_prior_data else rows
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=qrange(tab, f"A{next_row}"),
        valueInputOption="RAW", body={"values": payload},
    ).execute()


def results_row(date_s, time_s, project, keyword, intent, entity_type, entity_domain,
                rank, score, cited, top1, grounded, error="", answer_position=None,
                run_id="", answer_id="", ai_app="", model="", prompt_type="",
                repeat=1, mentioned=False, mention_position=None):
    def blank(v):
        return "" if v is None else v
    return [
        date_s, time_s, project, keyword, intent, entity_type, entity_domain,
        blank(rank), blank(score), "yes" if cited else "no", top1 or "",
        "yes" if grounded else "no", error or "", blank(answer_position),
        run_id, answer_id, ai_app, model or "", prompt_type, repeat,
        "yes" if mentioned else "no", blank(mention_position),
    ]
