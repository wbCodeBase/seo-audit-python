"""
/api/ai_rank — AI Visibility & Ranking Checker
Runs parallel Claude analyses to produce a full AI presence report for a domain:
  1. Domain knowledge profile (what Claude knows / doesn't know)
  2. Prompt ranking — top 10 results + domain position (if prompt given)
  3. Expected prompts — queries likely to surface this domain
  4. AI optimization suggestions — how to improve AI discoverability
"""

import json, os, re, threading
from http.server import BaseHTTPRequestHandler

CLAUDE_MODELS = ["claude-sonnet-4-5", "claude-3-5-sonnet-20241022"]


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

def _worker_domain(domain, api_key, bucket, key):
    clean = re.sub(r'^https?://', '', domain, flags=re.I)
    clean = re.sub(r'^www\.', '', clean, flags=re.I).rstrip('/')
    text, err = _call_claude(DOMAIN_PROMPT.format(domain=clean), api_key, max_tokens=2200)
    if err:
        bucket[key] = {"_error": err}
        return
    data = _parse_json(text)
    bucket[key] = data if data else {"_error": f"JSON parse failed. Raw: {(text or '')[:200]}"}


def _worker_ranking(prompt, api_key, bucket, key):
    text, err = _call_claude(RANKING_PROMPT.format(prompt=prompt), api_key, max_tokens=1800)
    if err:
        bucket[key] = {"_error": err}
        return
    data = _parse_json(text)
    if data and "rankings" in data:
        bucket[key] = data
    else:
        bucket[key] = {"_error": f"JSON parse failed. Raw: {(text or '')[:200]}"}


def _find_rank(rankings, domain):
    """Is this domain present as an entry in Claude's ranking answer? (domain-anchor check)"""
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
    """Is this exact brand name present (as title or domain) in Claude's ranking answer?"""
    if not brand or not rankings:
        return None
    needle = brand.strip().lower()
    for item in rankings:
        d = item.get("domain", "").lower()
        t = item.get("title", "").lower()
        if needle in t or needle in d:
            return item["rank"]
    return None


# ── Vercel handler ────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            domain = body.get("domain", "").strip()
            prompt = body.get("prompt", "").strip()
            brand  = body.get("brand", "").strip()

            if not domain:
                self._json(400, {"error": "Domain is required"})
                return

            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                self._json(200, {
                    "ok": True, "status": "no_key",
                    "error_detail": "ANTHROPIC_API_KEY is not set in Vercel environment variables.",
                })
                return

            bucket = {}

            if prompt:
                # Run domain analysis + ranking in parallel
                t1 = threading.Thread(target=_worker_domain,  args=(domain, api_key, bucket, "domain"))
                t2 = threading.Thread(target=_worker_ranking, args=(prompt,  api_key, bucket, "rank"))
                t1.daemon = t2.daemon = True
                t1.start(); t2.start()
                t1.join(55); t2.join(55)
            else:
                _worker_domain(domain, api_key, bucket, "domain")

            domain_result = bucket.get("domain") or {}
            rank_result   = bucket.get("rank")   or {}

            # Surface domain-analysis errors
            if domain_result.get("_error") and not rank_result:
                self._json(200, {
                    "ok": True, "status": "error",
                    "error_detail": domain_result["_error"]
                })
                return

            rankings   = rank_result.get("rankings", [])
            dom_rank   = _find_rank(rankings, domain) if prompt else None
            brand_rank = _find_rank_by_brand(rankings, brand) if (prompt and brand) else None

            self._json(200, {
                "ok":               True,
                "status":           "success",
                "domain":           domain,
                "domain_knowledge": domain_result.get("domain_knowledge", {}),
                "expected_prompts": domain_result.get("expected_prompts", []),
                "ai_suggestions":   domain_result.get("ai_suggestions", []),
                "prompt_ranking": {
                    "prompt":       prompt,
                    "query_intent": rank_result.get("query_intent", ""),
                    "rankings":     rankings,
                    "domain_rank":  dom_rank,
                    "brand":        brand,
                    "brand_rank":   brand_rank,
                } if prompt else None,
            })

        except Exception as e:
            self._json(500, {"error": str(e)})

    def _json(self, status, data):
        out = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a): pass
