"""
/api/scheduled_audit  GET
Triggered daily by Vercel Cron at 21:00 IST / 15:30 UTC (see vercel.json).
Also callable on demand — e.g. right after adding new keywords to the sheet,
without waiting for the schedule — by hitting this same URL with
?secret=<CRON_SECRET>.

Reads every active row from the "Keywords" tab of a Google Sheet, runs one
live web-search-grounded call per keyword PER enabled AI platform (Claude,
ChatGPT — shared across your domain + its brand + all its competitors per
platform, no extra API cost per competitor added), and appends one row per
entity to that platform's own Results tab ("Results - Claude",
"Results - ChatGPT"). Because it's a plain append every run, each tab
becomes a running daily history you can pivot or chart to compare yourself
against competitors, and Claude against ChatGPT, over time.

Adding a new keyword (or a new competitor, or a whole new client/domain) is
just editing the Keywords tab in Google Sheets — the next run (scheduled or
manual) picks it up automatically. No redeploy needed.

Which platforms actually run is controlled two ways:
  1. Whichever platform API keys are set in Vercel env vars — a platform
     with no key configured is skipped everywhere, silently.
  2. The optional per-row "Platforms" column in the Keywords tab (see
     below) — blank means "every configured platform", or list specific
     ones to restrict just that row/keyword.

Required Vercel env vars:
  ANTHROPIC_API_KEY            — enables the Claude platform
  OPENAI_API_KEY                — enables the ChatGPT platform
  (at least one of the two needs to be set — the other is simply skipped)
  GOOGLE_SHEET_ID               — the spreadsheet ID from its URL
  GOOGLE_SERVICE_ACCOUNT_JSON   — service account key JSON, base64-encoded
                                   (or raw JSON — both are accepted)
  CRON_SECRET                   — shared secret. Vercel Cron sends it
                                   automatically as "Authorization: Bearer
                                   <CRON_SECRET>"; pass ?secret=... yourself
                                   for a manual run

Keywords tab — row 1 is a header, data starts row 2:
  A Project | B Domain | C Brand | D Competitors (comma-separated domains) |
  E Keyword | F Active (TRUE/FALSE — blank counts as active) |
  G Platforms (comma-separated: claude, chatgpt — blank = all configured)

Results tab (one per platform) — header written automatically on first run:
  Date | Time (IST) | Project | Keyword | Intent | Entity Type |
  Entity Domain | Rank | Score | Cited In Answer | #1 Result | Grounded | Error
"""
import base64, json, os, re
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Deliberately self-contained rather than importing from ai_rank_run.py:
# Vercel's Python builder packages each api/*.py as its own isolated
# function, and every other file in this project avoids cross-file imports
# within api/ for that reason (see get_redis()/store_set() duplicated in
# audit_run.py, send_email.py, ai_rank_run.py, ai_rank_poll.py etc). A
# module-level `from ai_rank_run import ...` crashed the whole function at
# import time — before any of this file's own error handling could run.
# The Claude ranking helpers below are copies of ai_rank_run.py's; keep
# them in sync if that file's ranking logic changes.

MAX_WORKERS         = 6
IST                  = timezone(timedelta(hours=5, minutes=30))
CLAUDE_MODELS        = ["claude-sonnet-5", "claude-opus-4-8"]
OPENAI_MODELS        = ["gpt-5.1", "gpt-5", "gpt-4.1"]
WEB_SEARCH_MAX_USES  = 5  # Claude: cap searches per query; billed at $10/1,000 searches on top of tokens

RESULTS_HEADER = [
    "Date", "Time (IST)", "Project", "Keyword", "Intent",
    "Entity Type", "Entity Domain", "Rank", "Score",
    "Cited In Answer", "#1 Result", "Grounded", "Error",
]

RANKING_PROMPT = """\
{prompt}

Use web search to find current, real information before answering, and cite the sources you use. Answer the way you normally would for someone asking this question."""


# ── Shared helpers (platform-agnostic) ──────────────────────────────────────

def _domain_from_url(url):
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return re.sub(r'^www\.', '', netloc)


def _find_rank(rankings, domain):
    if not domain or not rankings:
        return None
    clean = re.sub(r'^https?://', '', domain, flags=re.I)
    clean = re.sub(r'^www\.', '', clean, flags=re.I).rstrip('/').lower()
    brand = clean.split('.')[0] if '.' in clean else clean
    for item in rankings:
        d = item.get("domain", "").lower()
        t = item.get("title", "").lower()
        if clean in d or d in clean or (brand and (brand in d or brand in t)):
            return item["rank"]
    return None


def _find_rank_by_brand(rankings, brand):
    if not brand or not rankings:
        return None
    needle = brand.strip().lower()
    for item in rankings:
        d = item.get("domain", "").lower()
        t = item.get("title", "").lower()
        if needle in t or needle in d:
            return item["rank"]
    return None


_LOCAL_HINTS = (
    "near me", "noida", "delhi", "mumbai", "bangalore", "bengaluru", "chennai",
    "hyderabad", "pune", "gurgaon", "gurugram", "kolkata", "ahmedabad", "jaipur",
    "in india", "in usa", "in uk", "in canada", "in dubai", "in uae",
)
_TRANSACTIONAL_HINTS = (
    "partners", "partner", "hire", "agency", "agencies", "consultant", "consultants",
    "company", "companies", "provider", "providers", "services", "service",
    "implementation", "pricing", "price", "cost", "quote", "buy", "vendor", "vendors",
)
_COMPARISON_HINTS = ("vs", "versus", "best", "top", "compare", "comparison", "alternative", "alternatives")
_INFORMATIONAL_HINTS = ("what is", "what are", "how to", "how does", "why", "guide", "tips", "meaning")


def _classify_intent(query):
    q = f" {query.strip().lower()} "
    if any(h in q for h in _LOCAL_HINTS):
        return "local"
    if any(h in q for h in _INFORMATIONAL_HINTS):
        return "informational"
    if any(h in q for h in _COMPARISON_HINTS):
        return "comparison"
    if any(h in q for h in _TRANSACTIONAL_HINTS):
        return "transactional"
    return "informational"


def _visibility_score(rank, grounded, cited):
    if not grounded:
        return None
    if not rank:
        return 0
    score = max(5, 100 - (rank - 1) * 12)
    if cited:
        score = min(100, score + 10)
    return score


def _cited_for_rank(rankings, rank):
    if not rank:
        return False
    for item in rankings or []:
        if item.get("rank") == rank:
            return bool(item.get("cited"))
    return False


# ── Claude platform ──────────────────────────────────────────────────────────

def _extract_rankings_claude(content_blocks):
    """Ranks from the actual web_search_tool_result data Claude's backend
    returned (the real search results), not just from inline citations —
    see ai_rank_run.py's _extract_rankings for the full rationale. Keeps a
    'cited' flag per item for whichever of those were also inline-cited."""
    seen = {}
    order = []
    cited_domains = set()
    searched = False

    for block in content_blocks:
        btype = getattr(block, "type", None)

        if btype == "server_tool_use":
            searched = True

        elif btype == "web_search_tool_result":
            searched = True
            content = getattr(block, "content", None) or []
            if not isinstance(content, list):
                continue
            for result in content:
                url = getattr(result, "url", None)
                if not url:
                    continue
                domain = _domain_from_url(url)
                if not domain or domain in seen:
                    continue
                seen[domain] = {
                    "domain": domain,
                    "title":  getattr(result, "title", "") or domain,
                    "url":    url,
                }
                order.append(domain)

        elif btype == "text":
            for c in (getattr(block, "citations", None) or []):
                if getattr(c, "type", None) != "web_search_result_location":
                    continue
                url = getattr(c, "url", None)
                domain = _domain_from_url(url) if url else ""
                if not domain:
                    continue
                cited_domains.add(domain)
                if domain not in seen:
                    seen[domain] = {
                        "domain": domain,
                        "title":  getattr(c, "title", "") or domain,
                        "url":    url,
                    }
                    order.append(domain)

    rankings = []
    for i, domain in enumerate(order):
        item = seen[domain]
        item["rank"]  = i + 1
        item["cited"] = domain in cited_domains
        rankings.append(item)

    return rankings, searched


def _rank_one_claude(prompt, api_key):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    last_err = ""
    for model in CLAUDE_MODELS:
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1800,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": WEB_SEARCH_MAX_USES,
                }],
                messages=[{"role": "user", "content": RANKING_PROMPT.format(prompt=prompt)}],
            )
            rankings, searched = _extract_rankings_claude(resp.content)
            answer = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return {"rankings": rankings, "grounded": searched, "answer": answer}
        except anthropic.NotFoundError:
            last_err = f"Model '{model}' not found"
            continue
        except anthropic.AuthenticationError:
            return {"_error": "Invalid ANTHROPIC_API_KEY — check your Vercel environment variables."}
        except anthropic.RateLimitError:
            return {"_error": "Rate limit reached — try again in a few seconds."}
        except anthropic.APITimeoutError:
            return {"_error": "Claude API timed out — try again."}
        except Exception as e:
            return {"_error": f"{type(e).__name__}: {e}"}
    return {"_error": f"No working model found. Last: {last_err}"}


# ── ChatGPT platform ─────────────────────────────────────────────────────────

def _extract_rankings_openai(output_items):
    """Mirrors _extract_rankings_claude but for the Responses API shape:
    raw ranked results come from each web_search_call item's
    action.sources[] (the actual search results OpenAI's backend returned —
    URL only, no title, so the domain is used as the title); inline
    citations come from url_citation annotations on output_text content,
    which set the 'cited' flag."""
    seen = {}
    order = []
    cited_domains = set()
    searched = False

    for item in output_items:
        itype = getattr(item, "type", None)

        if itype == "web_search_call":
            searched = True
            action  = getattr(item, "action", None)
            sources = getattr(action, "sources", None) or []
            for src in sources:
                url = getattr(src, "url", None)
                if not url:
                    continue
                domain = _domain_from_url(url)
                if not domain or domain in seen:
                    continue
                seen[domain] = {"domain": domain, "title": domain, "url": url}
                order.append(domain)

        elif itype == "message":
            for content in (getattr(item, "content", None) or []):
                if getattr(content, "type", None) != "output_text":
                    continue
                for ann in (getattr(content, "annotations", None) or []):
                    if getattr(ann, "type", None) != "url_citation":
                        continue
                    url = getattr(ann, "url", None)
                    domain = _domain_from_url(url) if url else ""
                    if not domain:
                        continue
                    cited_domains.add(domain)
                    if domain not in seen:
                        seen[domain] = {
                            "domain": domain,
                            "title":  getattr(ann, "title", "") or domain,
                            "url":    url,
                        }
                        order.append(domain)

    rankings = []
    for i, domain in enumerate(order):
        item = seen[domain]
        item["rank"]  = i + 1
        item["cited"] = domain in cited_domains
        rankings.append(item)

    return rankings, searched


def _rank_one_openai(prompt, api_key):
    import openai
    client = openai.OpenAI(api_key=api_key)
    last_err = ""
    for model in OPENAI_MODELS:
        try:
            resp = client.responses.create(
                model=model,
                input=RANKING_PROMPT.format(prompt=prompt),
                tools=[{"type": "web_search", "search_context_size": "high"}],
                max_output_tokens=1800,
            )
            rankings, searched = _extract_rankings_openai(resp.output)
            return {"rankings": rankings, "grounded": searched, "answer": resp.output_text}
        except openai.NotFoundError:
            last_err = f"Model '{model}' not found"
            continue
        except openai.AuthenticationError:
            return {"_error": "Invalid OPENAI_API_KEY — check your Vercel environment variables."}
        except openai.RateLimitError:
            return {"_error": "Rate limit reached — try again in a few seconds."}
        except openai.APITimeoutError:
            return {"_error": "OpenAI API timed out — try again."}
        except Exception as e:
            return {"_error": f"{type(e).__name__}: {e}"}
    return {"_error": f"No working model found. Last: {last_err}"}


# ── Platform registry ────────────────────────────────────────────────────────

def _available_platforms():
    """Only platforms whose API key is actually configured show up here —
    everything downstream (Keywords 'Platforms' column, tab creation, the
    audit loop) naturally skips whatever isn't available."""
    platforms = {}
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        platforms["claude"] = {
            "tab": "Results - Claude",
            "rank_fn": _rank_one_claude,
            "api_key": os.environ["ANTHROPIC_API_KEY"].strip(),
        }
    if os.environ.get("OPENAI_API_KEY", "").strip():
        platforms["chatgpt"] = {
            "tab": "Results - ChatGPT",
            "rank_fn": _rank_one_openai,
            "api_key": os.environ["OPENAI_API_KEY"].strip(),
        }
    return platforms


# ── Google Sheets ────────────────────────────────────────────────────────────

def _sheets_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
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


def _qrange(tab, cell_range):
    """Always single-quote the tab name in A1 notation — safe whether or
    not it contains spaces, and this project already got bitten once by an
    unverified Sheets-API assumption, so no shortcuts here."""
    return f"'{tab}'!{cell_range}"


def _read_keywords(svc, sheet_id, known_platforms):
    resp = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=_qrange("Keywords", "A2:G")
    ).execute()
    out = []
    for row in resp.get("values", []):
        row = row + [""] * (7 - len(row))
        project, domain, brand, competitors, keyword, active, platforms_cell = row[:7]
        domain, keyword = domain.strip(), keyword.strip()
        if not domain or not keyword:
            continue
        if active.strip().upper() == "FALSE":
            continue

        requested = [p.strip().lower() for p in platforms_cell.split(",") if p.strip()]
        platforms = [p for p in requested if p in known_platforms] or list(known_platforms)

        out.append({
            "project":     project.strip() or domain,
            "domain":      domain,
            "brand":       brand.strip(),
            "competitors": [c.strip() for c in competitors.split(",") if c.strip()],
            "keyword":     keyword,
            "platforms":   platforms,
        })
    return out


def _ensure_results_header(svc, sheet_id, tab):
    """Values.get/update can't create a missing tab — that needs a separate
    batchUpdate addSheet request. Create the tab first if it doesn't exist
    yet, then make sure it has a header row."""
    meta   = svc.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]

    if tab not in titles:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ).execute()
        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=_qrange(tab, "A1"),
            valueInputOption="RAW", body={"values": [RESULTS_HEADER]},
        ).execute()
        return

    resp = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=_qrange(tab, "A1:M1")
    ).execute()
    if not resp.get("values"):
        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=_qrange(tab, "A1"),
            valueInputOption="RAW", body={"values": [RESULTS_HEADER]},
        ).execute()


def _append_results(svc, sheet_id, tab, rows):
    if not rows:
        return
    svc.spreadsheets().values().append(
        spreadsheetId=sheet_id, range=_qrange(tab, "A1"),
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


# ── Audit ────────────────────────────────────────────────────────────────────

def _entity_row(date_s, time_s, project, keyword, intent, entity_type,
                 entity_domain, rank, score, cited, top1, grounded, error=""):
    return [
        date_s, time_s, project, keyword, intent, entity_type, entity_domain,
        rank if rank is not None else "", score if score is not None else "",
        "yes" if cited else "no", top1 or "", "yes" if grounded else "no", error,
    ]


def _audit_one(row, platform_key, platform_cfg):
    result = platform_cfg["rank_fn"](row["keyword"], platform_cfg["api_key"])
    return {"row": row, "platform": platform_key, "result": result}


def _run_scheduled_audit():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    if not sheet_id:
        return {"ok": False, "error": "GOOGLE_SHEET_ID not set."}

    platforms = _available_platforms()
    if not platforms:
        return {"ok": False, "error": "No platform API key configured. Set ANTHROPIC_API_KEY and/or OPENAI_API_KEY."}

    svc = _sheets_service()
    if not svc:
        return {"ok": False, "error": "GOOGLE_SERVICE_ACCOUNT_JSON not set or invalid."}

    keywords = _read_keywords(svc, sheet_id, platforms)
    if not keywords:
        return {"ok": True, "message": "No active rows found in the Keywords tab.", "rows_written": {}}

    for cfg in platforms.values():
        _ensure_results_header(svc, sheet_id, cfg["tab"])

    now_ist = datetime.now(IST)
    date_s, time_s = now_ist.strftime("%Y-%m-%d"), now_ist.strftime("%H:%M")

    out_rows = {p: [] for p in platforms}
    errors   = []

    tasks = [
        (row, p) for row in keywords for p in row["platforms"]
    ]

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(len(tasks), 1))) as pool:
        futures = {
            pool.submit(_audit_one, row, p, platforms[p]): (row, p) for row, p in tasks
        }
        for fut in as_completed(futures):
            row, platform_key = futures[fut]
            try:
                outcome = fut.result()
                result  = outcome["result"]
            except Exception as e:
                result = {"_error": f"{type(e).__name__}: {e}"}

            intent = _classify_intent(row["keyword"])

            if result.get("_error"):
                errors.append({"keyword": row["keyword"], "platform": platform_key, "error": result["_error"]})
                out_rows[platform_key].append(_entity_row(
                    date_s, time_s, row["project"], row["keyword"], intent, "own",
                    row["domain"], None, None, False, "", False, result["_error"],
                ))
                continue

            rankings = result.get("rankings", [])
            grounded = result.get("grounded", False)
            top1     = rankings[0]["domain"] if rankings else ""

            dom_rank  = _find_rank(rankings, row["domain"])
            dom_score = _visibility_score(dom_rank, grounded, _cited_for_rank(rankings, dom_rank))
            out_rows[platform_key].append(_entity_row(
                date_s, time_s, row["project"], row["keyword"], intent, "own",
                row["domain"], dom_rank, dom_score,
                _cited_for_rank(rankings, dom_rank), top1, grounded,
            ))

            if row["brand"]:
                brand_rank  = _find_rank_by_brand(rankings, row["brand"])
                brand_score = _visibility_score(brand_rank, grounded, _cited_for_rank(rankings, brand_rank))
                out_rows[platform_key].append(_entity_row(
                    date_s, time_s, row["project"], row["keyword"], intent, "own brand",
                    row["brand"], brand_rank, brand_score,
                    _cited_for_rank(rankings, brand_rank), top1, grounded,
                ))

            for comp in row["competitors"]:
                comp_rank  = _find_rank(rankings, comp)
                comp_score = _visibility_score(comp_rank, grounded, _cited_for_rank(rankings, comp_rank))
                out_rows[platform_key].append(_entity_row(
                    date_s, time_s, row["project"], row["keyword"], intent, "competitor",
                    comp, comp_rank, comp_score,
                    _cited_for_rank(rankings, comp_rank), top1, grounded,
                ))

    rows_written = {}
    for platform_key, cfg in platforms.items():
        _append_results(svc, sheet_id, cfg["tab"], out_rows[platform_key])
        rows_written[platform_key] = len(out_rows[platform_key])

    return {
        "ok":                 True,
        "platforms_run":      list(platforms.keys()),
        "keywords_processed": len(keywords),
        "rows_written":       rows_written,
        "errors":             errors,
    }


# ── Handler ──────────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        cron_secret = os.environ.get("CRON_SECRET", "").strip()
        if not cron_secret:
            return self._json(500, {"ok": False, "error": "CRON_SECRET is not configured in Vercel env vars."})

        qs            = parse_qs(urlparse(self.path).query)
        manual_secret = (qs.get("secret") or [""])[0]
        auth_header   = self.headers.get("Authorization", "")
        authorized    = auth_header == f"Bearer {cron_secret}" or manual_secret == cron_secret

        if not authorized:
            return self._json(401, {"ok": False, "error": "Unauthorized"})

        try:
            result = _run_scheduled_audit()
            self._json(200 if result.get("ok") else 500, result)
        except Exception as e:
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _json(self, code, data):
        out = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a): pass
