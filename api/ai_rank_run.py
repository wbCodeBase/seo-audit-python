"""
/api/ai_rank_run  POST {job_id, domain, brand, prompts}
Long-running worker for the AI Visibility & Ranking Checker.
Runs:
  1. Domain knowledge profile (once) — what Claude knows about the domain,
     expected prompts, AI optimization suggestions.
  2. Prompt ranking — one Claude "top 10" call per prompt (up to 50 prompts),
     run with bounded concurrency so we don't blow through Anthropic rate
     limits or the function's time budget.

Progress (completed / total) and results are written to Redis after every
prompt finishes, so /api/ai_rank_poll can show a live progress bar and — if
the function ever gets killed mid-run — the client still sees whatever
prompts completed rather than nothing at all.
"""

import json, os, re, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler

CLAUDE_MODELS = ["claude-sonnet-4-5", "claude-3-5-sonnet-20241022"]
MAX_WORKERS   = 6
PER_CALL_TIMEOUT = 55


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
You are a search ranking engine. For the query below, list the top 10 most relevant websites or businesses.

Query: {prompt}

Return ONLY valid JSON (no markdown):

{{
  "query_intent": "One sentence: what the searcher wants",
  "rankings": [
    {{
      "rank": 1,
      "title": "Brand or Website Name",
      "domain": "example.com",
      "description": "1-2 sentences: what this offers and why it ranks here"
    }}
  ]
}}

- domain: root domain only (no http / www / trailing slash)
- For local queries, list real local businesses if you know them
- Provide exactly 10 results"""


# ── Workers ───────────────────────────────────────────────────────────────────

def _worker_domain(domain, api_key, bucket):
    clean = re.sub(r'^https?://', '', domain, flags=re.I)
    clean = re.sub(r'^www\.', '', clean, flags=re.I).rstrip('/')
    text, err = _call_claude(DOMAIN_PROMPT.format(domain=clean), api_key, max_tokens=2200)
    if err:
        bucket["domain"] = {"_error": err}
        return
    data = bucket["domain"] = _parse_json(text) or {"_error": f"JSON parse failed. Raw: {(text or '')[:200]}"}


def _rank_one(prompt, api_key):
    text, err = _call_claude(RANKING_PROMPT.format(prompt=prompt), api_key, max_tokens=1800)
    if err:
        return {"_error": err}
    data = _parse_json(text)
    if data and "rankings" in data:
        return data
    return {"_error": f"JSON parse failed. Raw: {(text or '')[:200]}"}


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
                    prompt_reports[idx] = {
                        "prompt":       prompt,
                        "query_intent": result.get("query_intent", ""),
                        "rankings":     rankings,
                        "domain_rank":  dom_rank,
                        "brand_rank":   brand_rank,
                        "error":        result.get("_error"),
                    }
                    completed += 1
                    _persist_progress()

    t_domain.join(PER_CALL_TIMEOUT)
    domain_result = domain_bucket.get("domain") or {"_error": "Domain analysis timed out."}

    found_reports = [p for p in prompt_reports if p is not None and not p.get("error")]
    domain_found  = [p for p in found_reports if p.get("domain_rank")]
    brand_found   = [p for p in found_reports if brand and p.get("brand_rank")]

    summary = {
        "total_prompts":       total,
        "completed_prompts":   len(found_reports),
        "domain_found_count":  len(domain_found),
        "brand_found_count":   len(brand_found) if brand else None,
        "avg_domain_rank":     round(sum(p["domain_rank"] for p in domain_found) / len(domain_found), 1)
                                if domain_found else None,
        "avg_brand_rank":      round(sum(p["brand_rank"] for p in brand_found) / len(brand_found), 1)
                                if brand_found else None,
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
