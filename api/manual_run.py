"""
/api/manual_run  POST (JSON)
Job API behind the web app's "Run now" button and nightly cron. Each call does
a small piece of work so it stays well inside the serverless time limit:

  {"action": "plan"}                                        → clients with active questions
  {"action": "start",  "client_id": 5, "trigger": "manual", "sync": true}
                                                            → run_id + tasks
  {"action": "task",   "run_id": "...", "question_id": 12, "app": "claude", "repeat": 1}
                                                            → one AI answer saved
  {"action": "finish", "run_id": "...", "metrics": true}    → run closed, sheet + metrics updated

Auth: header "Authorization: Bearer <CRON_SECRET>" (server-to-server only —
the browser never sees the secret; the web app checks the user is an admin
before calling). Logic lives in geo/jobs.py.
"""
import hmac, json, os, sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

MAX_BODY = 16 * 1024


def _int(body, key):
    v = body.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{key} must be an integer")
    return v


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        secret = os.environ.get("CRON_SECRET", "").strip()
        if not secret:
            return self._json(500, {"ok": False, "error": "CRON_SECRET is not configured."})
        given = self.headers.get("Authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {secret}".encode()):
            return self._json(401, {"ok": False, "error": "Unauthorized"})

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json(413, {"ok": False, "error": "Request too large"})
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(400, {"ok": False, "error": f"Bad request: {e}"})

        try:
            from geo import jobs
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"Could not load the geo package: {type(e).__name__}: {e}"})

        action = body.get("action")
        try:
            if action == "plan":
                result = jobs.plan()
            elif action == "start":
                trigger = body.get("trigger") if body.get("trigger") in ("manual", "cron") else "manual"
                result = jobs.start(_int(body, "client_id"), trigger=trigger, sync=bool(body.get("sync", True)))
            elif action == "task":
                app = body.get("app")
                if not isinstance(app, str) or not isinstance(body.get("run_id"), str):
                    raise ValueError("run_id and app must be strings")
                result = jobs.task(body["run_id"], _int(body, "question_id"), app,
                                   repeat=_int(body, "repeat") if "repeat" in body else 1)
            elif action == "finish":
                if not isinstance(body.get("run_id"), str):
                    raise ValueError("run_id must be a string")
                result = jobs.finish(body["run_id"], metrics=bool(body.get("metrics", True)))
            else:
                return self._json(400, {"ok": False, "error": "Unknown action"})
        except ValueError as e:
            return self._json(400, {"ok": False, "error": f"Bad request: {e}"})
        except jobs.JobError as e:
            return self._json(422, {"ok": False, "error": str(e)})
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        self._json(200, result)

    def do_GET(self):
        self._json(405, {"ok": False, "error": "Use POST"})

    def _json(self, code, data):
        out = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a): pass
