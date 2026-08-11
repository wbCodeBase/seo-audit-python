"""
/api/ai_rank_run  POST {job_id, domain, brand, prompts}
Long-running worker for the AI Visibility & Ranking Checker.
Runs:
  1. Domain knowledge profile (once) — what Claude knows about the domain,
     expected prompts, AI optimization suggestions.
  2. Prompt ranking — one live web-search-grounded call per prompt (up to
     50 prompts), reporting the sources Claude actually cited (not a
     recalled guess), run with bounded concurrency so we don't blow through
     Anthropic rate limits or the function's time budget.

Progress (completed / total) and results are written to Redis after every
prompt finishes, so /api/ai_rank_poll can show a live progress bar and — if
the function ever gets killed mid-run — the client still sees whatever
prompts completed rather than nothing at all.
"""

import json, os, re, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

CLAUDE_MODELS = ["claude-sonnet-5", "claude-opus-4-8"]
MAX_WORKERS   = 6
PER_CALL_TIMEOUT = 55
WEB_SEARCH_MAX_USES = 5  # cap searches per query; billed at $10/1,000 searches on top of tokens


# ── Redis ─────────────────────────────────────────────────────────────────────

def get_redis():
    url   = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        return None
    try:
        from upstash_redis import Redis
        return Redis(url=url, token=token)
    except Exception:
        return None


def store_set(job_id, value):
    r = get_redis()
    if r:
        r.set(f"airank:{job_id}", json.dumps(value), ex=3600)


# ── Low-level Claude call ─────────────────────────────────────────────────────

def _call_claude(prompt_text, api_key, max_tokens=2000):
    """Returns (text, error_string)."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    last_err = ""
    for model in CLAUDE_MODELS:
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt_text}],
            )
            return "".join(b.text for b in resp.content if b.type == "text"), None
        except anthropic.NotFoundError:
            last_err = f"Model '{model}' not found"
            continue
        except anthropic.AuthenticationError:
            return None, "Invalid ANTHROPIC_API_KEY — check your Vercel environment variables."
        except anthropic.RateLimitError:
            return None, "Rate limit reached — try again in a few seconds."
        except anthropic.APITimeoutError:
            return None, "Claude API timed out — try again."
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"
    return None, f"No working model found. Last: {last_err}"


def _parse_json(text):
    if not text:
        return None
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ── Prompt templates ──────────────────────────────────────────────────────────

DOMAIN_PROMPT = """\
You are an AI knowledge auditor. Analyze the domain "{domain}" deeply.

Return ONLY valid JSON (no markdown fences):

{{
  "domain_knowledge": {{
    "is_known": true,
    "name": "Official brand or company name",
    "tagline": "Short tagline or value proposition (empty string if unknown)",
    "description": "2-3 sentences about what this domain does — based strictly on your training data",
    "industry": "Primary industry (e.g. Digital Marketing, E-commerce, Healthcare)",
    "sub_industry": "More specific niche if known",
    "location": "City, Country if known — else empty string",
    "founded": "Year founded if known — else empty string",
    "services": ["up to 6 services or products offered"],
    "target_audience": "Who their customers are (1 sentence)",
    "unique_strengths": ["up to 3 competitive strengths"],
    "key_facts": ["up to 5 notable facts Claude knows about this domain"],
    "data_gaps": ["up to 5 things Claude does NOT know — be specific"],
    "online_presence": "strong / moderate / weak / unknown",
    "confidence_level": "high / medium / low / unknown",
    "indexed_topics": ["up to 5 topic areas where this domain is associated in AI training data"]
  }},
  "expected_prompts": [
    {{
      "prompt": "Exact search query that would surface this domain in Claude responses",
      "likelihood": "high / medium / low",
      "reason": "One sentence: why this query would show this domain",
      "intent": "informational / transactional / local / comparison / navigational"
    }}
  ],
  "ai_suggestions": [
    {{
      "priority": "high / medium / low",
      "category": "Content / Technical / Authority / Social / Local / Schema",
      "title": "Short action title (5-8 words)",
      "description": "Specific actionable advice (2-3 sentences) for improving AI discoverability"
    }}
  ]
}}

Rules:
- is_known: false if this domain is not in your training data
- Even if unknown, infer from the domain name pattern and fill expected_prompts / ai_suggestions
- expected_prompts: provide exactly 6, covering different intents (local, comparison, informational, transactional)
- ai_suggestions: provide exactly 6 prioritized tips specific to this domain's gaps
- confidence_level: "high" = well-known global brand, "medium" = regional/niche known, "low" = barely known, "unknown" = not in training data"""


RANKING_PROMPT = """\
{prompt}

Use web search to find current, real information before answering, and cite the sources you use. Answer the way you normally would for someone asking this question."""


# ── Workers ───────────────────────────────────────────────────────────────────

def _worker_domain(domain, api_key, bucket):
    clean = re.sub(r'^https?://', '', domain, flags=re.I)
    clean = re.sub(r'^www\.', '', clean, flags=re.I).rstrip('/')
    text, err = _call_claude(DOMAIN_PROMPT.format(domain=clean), api_key, max_tokens=2200)
    if err:
        bucket["domain"] = {"_error": err}
        return
    data = bucket["domain"] = _parse_json(text) or {"_error": f"JSON parse failed. Raw: {(text or '')[:200]}"}


def _domain_from_url(url):
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return re.sub(r'^www\.', '', netloc)


def _extract_rankings(content_blocks):
    """Walk a web-search-enabled response and build a ranked list of domains.

    Previously this only looked at inline `citations` on the text blocks —
    i.e. the handful of sources Claude happened to quote in its prose. That
    undercounts badly: Claude often mentions a company by name without
    attaching an inline citation marker to that sentence, so a domain that
    plainly appears in the answer (and in the underlying search results)
    would still be scored "not cited". The `web_search_tool_result` block
    carries the actual ranked result list the search backend returned for
    each query Claude issued — that's the real SERP-like data and is what we
    rank against now. Inline citations are kept as a "cited" flag per item
    so the UI can still show which sources Claude directly quoted.
    Also reports whether Claude searched at all, so 'not found' can be told
    apart from 'never grounded in a search'."""
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
                continue  # WebSearchToolResultError — search failed for this call
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


def _rank_one(prompt, api_key):
    """Ask Claude the query with live web search enabled and report the
    ranked results the search backend actually returned — the same mechanism
    AI-visibility tools like Otterly use, instead of asking the model to
    recall a top-10 list from memory."""
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
            rankings, searched = _extract_rankings(resp.content)
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
    """Lightweight, deterministic keyword-intent classifier — no extra API
    call needed. Order matters: local/transactional service-seeking queries
    ('X partners in Noida') should win over a generic 'best/top' comparison
    match, since those words can co-occur."""
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
    """0-100 AI Visibility Score for one query, built only from data we
    already have (no keyword-volume API): higher rank position scores
    higher, an inline citation in Claude's answer adds a bonus, not being
    found at all (while the query WAS actually searched live) scores 0.
    None means we have no signal because Claude never grounded the answer
    in a search for this query, so scoring it would be meaningless."""
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


def _run_all(job_id, domain, brand, prompts):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    total   = len(prompts)

    domain_bucket = {}
    t_domain = threading.Thread(target=_worker_domain, args=(domain, api_key, domain_bucket))
    t_domain.daemon = True
    t_domain.start()

    prompt_reports = [None] * total
    completed = 0
    lock = threading.Lock()

    def _persist_progress():
        store_set(job_id, {
            "status":         "running",
            "domain":         domain,
            "brand":          brand,
            "total":          total,
            "completed":      completed,
            "prompt_reports": [p for p in prompt_reports if p is not None],
        })

    if total:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, total)) as pool:
            future_to_idx = {
                pool.submit(_rank_one, p, api_key): i for i, p in enumerate(prompts)
            }
            for fut in as_completed(future_to_idx):
                idx    = future_to_idx[fut]
                prompt = prompts[idx]
                try:
                    result = fut.result()
                except Exception as e:
                    result = {"_error": f"{type(e).__name__}: {e}"}

                rankings   = result.get("rankings", []) if not result.get("_error") else []
                dom_rank   = _find_rank(rankings, domain)
                brand_rank = _find_rank_by_brand(rankings, brand) if brand else None

                with lock:
                    grounded = result.get("grounded", False)
                    prompt_reports[idx] = {
                        "prompt":       prompt,
                        "intent":       _classify_intent(prompt),
                        "rankings":     rankings,
                        "domain_rank":  dom_rank,
                        "brand_rank":   brand_rank,
                        "score":        _visibility_score(dom_rank, grounded, _cited_for_rank(rankings, dom_rank)),
                        "grounded":     grounded,
                        "answer":       result.get("answer", ""),
                        "error":        result.get("_error"),
                    }
                    completed += 1
                    _persist_progress()

    t_domain.join(PER_CALL_TIMEOUT)
    domain_result = domain_bucket.get("domain") or {"_error": "Domain analysis timed out."}

    found_reports = [p for p in prompt_reports if p is not None and not p.get("error")]
    domain_found  = [p for p in found_reports if p.get("domain_rank")]
    brand_found   = [p for p in found_reports if brand and p.get("brand_rank")]
    grounded      = [p for p in found_reports if p.get("grounded")]
    scored        = [p for p in found_reports if p.get("score") is not None]

    summary = {
        "total_prompts":       total,
        "completed_prompts":   len(found_reports),
        "grounded_count":      len(grounded),
        "domain_found_count":  len(domain_found),
        "brand_found_count":   len(brand_found) if brand else None,
        "avg_domain_rank":     round(sum(p["domain_rank"] for p in domain_found) / len(domain_found), 1)
                                if domain_found else None,
        "avg_brand_rank":      round(sum(p["brand_rank"] for p in brand_found) / len(brand_found), 1)
                                if brand_found else None,
        "avg_score":           round(sum(p["score"] for p in scored) / len(scored), 1)
                                if scored else None,
    }

    store_set(job_id, {
        "status": "done",
        "data": {
            "domain":           domain,
            "brand":            brand,
            "domain_knowledge": domain_result.get("domain_knowledge", {}) if not domain_result.get("_error") else {},
            "expected_prompts": domain_result.get("expected_prompts", []) if not domain_result.get("_error") else [],
            "ai_suggestions":   domain_result.get("ai_suggestions", []) if not domain_result.get("_error") else [],
            "domain_error":     domain_result.get("_error"),
            "prompt_reports":   [p for p in prompt_reports if p is not None],
            "summary":          summary,
        },
    })


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        job_id = ""
        try:
            length  = int(self.headers.get("Content-Length", 0))
            body    = json.loads(self.rfile.read(length) or b"{}")
            job_id  = body.get("job_id", "")
            domain  = body.get("domain", "").strip()
            brand   = body.get("brand", "").strip()
            prompts = body.get("prompts", [])

            if job_id and domain:
                _run_all(job_id, domain, brand, prompts)

        except Exception as e:
            if job_id:
                store_set(job_id, {"status": "error", "message": str(e)})

        out = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a): pass
