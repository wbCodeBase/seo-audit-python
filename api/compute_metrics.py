"""
/api/compute_metrics  GET
Triggered nightly by Vercel Cron at 22:00 IST / 16:30 UTC (an hour after the
audit — see vercel.json). Also callable on demand with ?secret=<CRON_SECRET>,
optionally &days=N to recompute a longer window.

Scores sentiment for new answers that name the client (needs
ANTHROPIC_API_KEY; skipped otherwise), then rebuilds daily_metrics and
daily_competitor_metrics for the last METRICS_DAYS days. Logic: geo/metrics.py.
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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
        if not (auth_header == f"Bearer {cron_secret}" or manual_secret == cron_secret):
            return self._json(401, {"ok": False, "error": "Unauthorized"})

        try:
            days = int((qs.get("days") or ["0"])[0]) or None
        except ValueError:
            return self._json(400, {"ok": False, "error": "days must be a number"})

        try:
            from geo.metrics import run
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"Could not load the geo package: {type(e).__name__}: {e}"})

        try:
            result = run(days=days)
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
