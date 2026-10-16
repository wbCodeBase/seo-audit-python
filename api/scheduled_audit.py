"""
/api/scheduled_audit  GET
Triggered daily by Vercel Cron at 21:00 IST / 15:30 UTC (see vercel.json).
Also callable on demand — e.g. right after adding new keywords to the sheet —
with ?secret=<CRON_SECRET>.

All the audit logic lives in the shared geo/ package (geo/audit.py) so the
same code serves this cron, local runs (python -m geo run), the history
import and — later — the job-queue workers. Vercel's Python builder bundles
the whole repo (minus vercel.json's excludeFiles), so geo/ ships with it.

What a run does: reads active rows from the "Keywords" and "Questions" tabs,
asks every enabled AI app (Claude, ChatGPT) each one with live web search,
scores your domain, brand and competitors, and saves the results to Postgres
(DATABASE_URL) and/or the "Results - <App>" tabs (SHEETS_DUAL_WRITE). See
README.md and AUDIT_USER_MANUAL.md for the columns and env vars.

Env vars: ANTHROPIC_API_KEY / OPENAI_API_KEY (at least one), GOOGLE_SHEET_ID,
GOOGLE_SERVICE_ACCOUNT_JSON, CRON_SECRET, and optionally DATABASE_URL,
SHEETS_DUAL_WRITE, REPEATS_PER_RUN, DISCOVERY_ENABLED — see .env.example.
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Vercel packages each api/*.py as its own function; the project root (where
# geo/ lives) isn't on sys.path by default. Import lazily inside the handler
# so a packaging problem returns a readable JSON error instead of crashing
# the function at import time (which is what happened once before with a
# module-level cross-file import).
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


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
            from geo.audit import run
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"Could not load the geo package: {type(e).__name__}: {e}"})

        try:
            result = run(trigger="manual" if manual_secret else "cron")
            self._json(200 if result.get("ok") else 500, result)
        except Exception as e:
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _json(self, code, data):
        out = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a): pass
