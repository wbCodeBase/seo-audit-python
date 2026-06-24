"""
/api/ai_rank — AI Ranking Checker
Asks Claude to rank the top websites for a given query (structured JSON response).
Returns rank position, title, domain, description for each result.
Shows where the user's domain appears in Claude's rankings.
"""

import json, os, re
from http.server import BaseHTTPRequestHandler

CLAUDE_MODELS = [
    "claude-sonnet-4-5",
    "claude-3-5-sonnet-20241022",
]

RANKING_PROMPT = """\
You are a search ranking engine. For the query below, list the top 10 most relevant websites, services, or businesses.

Query: {prompt}

Rules:
- Rank by relevance and quality
- Use real, specific websites or businesses that actually exist
- For local queries (e.g. "in Noida", "near me"), list real local businesses
- "domain" must be the root domain only: e.g. "example.com" — no http, no www, no trailing slash
- "title" is the official brand/website name
- "description" is 1-2 sentences: what it offers and why it ranks here

Return ONLY valid JSON, no markdown fences, no explanation:

{{
  "query_intent": "one sentence describing what the searcher wants",
  "rankings": [
    {{
      "rank": 1,
      "title": "Brand or Website Name",
      "domain": "example.com",
      "description": "What this offers and why it is relevant to the query."
    }}
  ]
}}

Provide exactly 10 results."""


def query_claude(prompt, api_key):
    """Returns (data_dict, error_string)."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = RANKING_PROMPT.format(prompt=prompt)

    last_err = ""
    for model in CLAUDE_MODELS:
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1800,
                messages=[{"role": "user", "content": msg}],
            )
            raw = "".join(b.text for b in resp.content if b.type == "text")

            m = re.search(r'\{[\s\S]*\}', raw)
            if not m:
                return None, f"Claude returned no JSON. Raw start: {raw[:200]}"

            data = json.loads(m.group(0))
            if "rankings" not in data:
                return None, "Claude JSON missing 'rankings' key."

            return data, None

        except anthropic.NotFoundError:
            last_err = f"Model '{model}' not found"
            continue
        except json.JSONDecodeError as e:
            return None, f"JSON parse error: {e}"
        except anthropic.AuthenticationError:
            return None, "Invalid ANTHROPIC_API_KEY — check your Vercel environment variables."
        except anthropic.RateLimitError:
            return None, "Rate limit reached — please try again in a few seconds."
        except anthropic.APITimeoutError:
            return None, "Claude API timed out — try a shorter or simpler query."
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

    return None, f"No working Claude model found. Last: {last_err}"


def find_domain_rank(rankings, domain):
    """Return rank number if domain matches any result, else None."""
    if not domain or not rankings:
        return None

    clean = re.sub(r'^https?://', '', domain, flags=re.I)
    clean = re.sub(r'^www\.', '', clean, flags=re.I)
    clean = clean.rstrip('/').lower()
    brand = clean.split('.')[0] if '.' in clean else clean

    for item in rankings:
        d = item.get("domain", "").lower()
        t = item.get("title", "").lower()
        if clean in d or d in clean or (brand and (brand in d or brand in t)):
            return item["rank"]
    return None


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
            prompt = body.get("prompt", "").strip()
            domain = body.get("domain", "").strip()

            if not prompt:
                self._json(400, {"error": "Prompt is required"})
                return

            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                self._json(200, {
                    "ok": True, "status": "no_key",
                    "error_detail": "ANTHROPIC_API_KEY is not set in Vercel environment variables.",
                })
                return

            data, err = query_claude(prompt, api_key)

            if err or not data:
                self._json(200, {
                    "ok": True, "status": "error",
                    "error_detail": err or "Unknown error",
                })
                return

            rankings    = data.get("rankings", [])
            domain_rank = find_domain_rank(rankings, domain)

            self._json(200, {
                "ok":           True,
                "status":       "success",
                "query_intent": data.get("query_intent", ""),
                "rankings":     rankings,
                "domain_rank":  domain_rank,
                "domain":       domain,
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
