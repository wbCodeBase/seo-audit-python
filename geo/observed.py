"""
Observed engines: the answer a real user sees, captured through DataForSEO.

  chatgpt_web     /v3/ai_optimization/chat_gpt/llm_scraper/live/advanced
  gemini_web      /v3/ai_optimization/gemini/llm_scraper/live/advanced
  google_aio      /v3/serp/google/organic/live/advanced  (the ai_overview item)
  google_ai_mode  /v3/serp/google/ai_mode/live/advanced

We do not drive a browser against chatgpt.com or Google ourselves: that breaks
those sites' terms, gets accounts and IPs blocked, and needs constant repair.
The provider runs that infrastructure, bills per result (a fraction of a cent)
and returns structured JSON, which we turn into the same ParsedAnswer the API
engines produce — so matching, scoring, discovery and metrics are shared.

Field mapping into ParsedAnswer:
  answer_text  result.markdown (or the text items joined)
  rankings     search_results (everything retrieved) in order, then any cited
               source not among them; rank = that order
  citations    sources / references the answer actually cites, in order
  grounded     True when any web result or source came back
ChatGPT's response also lists brand_entities, which replace the paid
LLM brand-extraction pass for those answers.

Auth: DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD (HTTP Basic). Location per
question from questions.country (ISO code), else DATAFORSEO_LOCATION_CODE.
"""
import base64
import json
import urllib.error
import urllib.request

from geo.parsing import ParsedAnswer, _Collector

API_BASE = "https://api.dataforseo.com/v3"
TIMEOUT_S = 150   # live LLM scraper calls take up to ~120 s

PATHS = {
    "chatgpt_web":    "/ai_optimization/chat_gpt/llm_scraper/live/advanced",
    "gemini_web":     "/ai_optimization/gemini/llm_scraper/live/advanced",
    "google_aio":     "/serp/google/organic/live/advanced",
    "google_ai_mode": "/serp/google/ai_mode/live/advanced",
}

# ISO country → DataForSEO location_code (the common ones; anything else falls
# back to the default). Full list: /v3/serp/google/locations.
LOCATION_CODES = {
    "us": 2840, "in": 2356, "gb": 2826, "uk": 2826, "ca": 2124, "au": 2036,
    "ae": 2784, "sg": 2702, "de": 2276, "fr": 2250, "nz": 2554, "za": 2710,
}

_BILLING_CODES = {40200, 40210}    # payment required / insufficient balance
_AUTH_CODES = {40100, 40101, 40102}


class ObservedError(Exception):
    pass


def location_code(country, default):
    return LOCATION_CODES.get((country or "").strip().lower(), default)


def build_task(app, keyword, loc_code, language="en"):
    task = {"keyword": keyword[:700] if app.startswith("google") else keyword[:2000],
            "location_code": loc_code, "language_code": language}
    if app == "chatgpt_web":
        task["force_web_search"] = True
    if app == "google_aio":
        # Without this, Google's lazily-loaded AI Overviews come back null.
        # Extra charge is refunded when there's no async overview.
        task["load_async_ai_overview"] = True
        task["depth"] = 10
    return task


def _post(path, task, login, password, opener=None):
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    req = urllib.request.Request(
        API_BASE + path, data=json.dumps([task]).encode(), method="POST",
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ObservedError("DataForSEO rejected the login — check DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD.")
        if e.code == 402:
            raise ObservedError("DataForSEO account balance is empty — top up at app.dataforseo.com.")
        raise ObservedError(f"DataForSEO HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ObservedError(f"Couldn't reach DataForSEO: {getattr(e, 'reason', e)}")
    except ValueError:
        raise ObservedError("DataForSEO returned invalid JSON")


def unwrap(body):
    """(result dict or None, cost USD) from a one-task live response; raises
    ObservedError with a human message on any API-level failure."""
    code = body.get("status_code")
    if code in _AUTH_CODES:
        raise ObservedError("DataForSEO rejected the login — check DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD.")
    if code in _BILLING_CODES:
        raise ObservedError("DataForSEO account balance is empty — top up at app.dataforseo.com.")
    if code != 20000:
        raise ObservedError(f"DataForSEO error {code}: {body.get('status_message', '')}".strip())
    tasks = body.get("tasks") or []
    if not tasks:
        raise ObservedError("DataForSEO returned no task")
    t = tasks[0]
    cost = float(t.get("cost") or body.get("cost") or 0)
    if t.get("status_code") in _BILLING_CODES:
        raise ObservedError("DataForSEO account balance is empty — top up at app.dataforseo.com.")
    if t.get("status_code") != 20000:
        raise ObservedError(f"DataForSEO task error {t.get('status_code')}: {t.get('status_message', '')}".strip())
    results = t.get("result") or []
    return (results[0] if results else None), cost


# ── Parsers (pure) ──────────────────────────────────────────────────────────

def _texts(items, types):
    return "\n\n".join(i.get("markdown") or i.get("text") or "" for i in items or []
                       if i.get("type") in types and (i.get("markdown") or i.get("text"))).strip()


def parse_llm_scraper(result, text_types=("chat_gpt_text", "chat_gpt_table", "gemini_text", "gemini_table")):
    """ChatGPT / Gemini real-UI answer → (ParsedAnswer, brands or None)."""
    if not result:
        return ParsedAnswer(), None
    items = result.get("items") or []
    answer = (result.get("markdown") or "").strip() or _texts(items, text_types)

    sources = list(result.get("sources") or [])
    if not sources:
        for it in items:
            sources.extend(it.get("sources") or [])

    col = _Collector()
    for r in result.get("search_results") or []:
        col.add_result(r.get("url"), r.get("title"))
    for s in sources:
        col.add_citation(s.get("url"), s.get("title"))
    col.searched = bool(col.order)

    brands = None
    entities = list(result.get("brand_entities") or [])
    for it in items:
        entities.extend(it.get("brand_entities") or [])
    if entities:
        brands, seen = [], set()
        for b in entities:
            name = (b.get("title") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            urls = b.get("urls") or []
            brands.append({"name": name, "domain": (urls[0].get("domain") or "") if urls else ""})

    return ParsedAnswer(rankings=col.rankings(), grounded=col.searched, answer_text=answer,
                        citations=list(col.cited_urls.values())), brands


def _find_overview(result):
    for it in (result or {}).get("items") or []:
        if it.get("type") == "ai_overview":
            return it
    return None


def parse_overview(result):
    """Google AI Overview / AI Mode → (ParsedAnswer, None, shown?).
    No overview on the page is a real result (users saw none), not an error."""
    ov = _find_overview(result)
    if ov is None:
        return ParsedAnswer(), None, False
    elements = ov.get("items") or []
    answer = (ov.get("markdown") or "").strip() or _texts(elements, ("ai_overview_element",))

    col = _Collector()
    refs = list(ov.get("references") or [])
    for el in elements:
        refs.extend(el.get("references") or [])
        refs.extend(el.get("links") or [])
    for r in refs:
        col.add_citation(r.get("url"), r.get("title") or r.get("source"))
    col.searched = bool(col.order)
    return ParsedAnswer(rankings=col.rankings(), grounded=col.searched, answer_text=answer,
                        citations=list(col.cited_urls.values())), None, True


# ── Entry point used by the provider registry ──────────────────────────────

def observe(app, keyword, login, password, loc_code, opener=None):
    """Returns (ParsedAnswer, brands, cost_usd, info dict). Raises ObservedError."""
    body = _post(PATHS[app], build_task(app, keyword, loc_code), login, password, opener=opener)
    result, cost = unwrap(body)
    info = {"location_code": loc_code}
    if app in ("google_aio", "google_ai_mode"):
        parsed, brands, shown = parse_overview(result)
        info["ai_overview_shown"] = shown
    else:
        parsed, brands = parse_llm_scraper(result)
        if result and result.get("fan_out_queries"):
            info["fan_out_queries"] = list(result["fan_out_queries"])[:10]
    model = (result or {}).get("model") or app
    return parsed, brands, cost, {**info, "model": model}
