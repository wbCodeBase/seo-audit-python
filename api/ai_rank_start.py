"""
/api/ai_rank_start  POST {domain, brand, prompts}
Validates input, creates job_id, saves initial job state to Redis, returns job_id.
The frontend then calls /api/ai_rank_run directly as a separate long fetch,
and polls /api/ai_rank_poll for progress + the final report.

`prompts` accepts either a list of strings or a single string with one
query per line (or comma-separated) — up to MAX_PROMPTS queries.
"""
import json, os, re, uuid
from http.server import BaseHTTPRequestHandler

MAX_PROMPTS = 50


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


def parse_prompts(raw):
    """Accepts a list or a delimited string (newlines and/or commas)."""
    if isinstance(raw, list):
        items = raw
    else:
        text = str(raw or "")
        # Split on newlines first; if a "line" still has commas, split those too.
        items = []
        for line in text.splitlines():
            parts = line.split(",") if "," in line else [line]
            items.extend(parts)

    seen, out = set(), []
    for p in items:
        p = re.sub(r"\s+", " ", str(p or "")).strip()
        if not p or p.lower() in seen:
            continue
        seen.add(p.lower())
        out.append(p)
        if len(out) >= MAX_PROMPTS:
            break
    return out


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length) or b"{}")
            domain  = body.get("domain", "").strip()
            brand   = body.get("brand", "").strip()
            prompts = parse_prompts(body.get("prompts", body.get("prompt", "")))

            if not domain:
                return self._json(400, {"error": "Domain is required"})

            if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
                return self._json(200, {
                    "job_id": None,
                    "error": "ANTHROPIC_API_KEY is not set in Vercel environment variables.",
                })

            job_id = str(uuid.uuid4())
            store_set(job_id, {
                "status":    "running",
                "domain":    domain,
                "brand":     brand,
                "prompts":   prompts,
                "total":     len(prompts),
                "completed": 0,
            })

            self._json(200, {"job_id": job_id, "total": len(prompts)})

        except Exception as e:
            self._json(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, code, data):
        out = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *a): pass
