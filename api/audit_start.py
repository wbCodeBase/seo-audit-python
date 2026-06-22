"""
/api/audit_start  POST {url, name, email}
Just validates input, creates job_id, saves to Redis, returns job_id.
The frontend calls /api/audit_run directly as a separate long fetch.
"""
import json, uuid, os
from http.server import BaseHTTPRequestHandler


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
        r.set(f"seo:{job_id}", json.dumps(value), ex=3600)


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            url    = body.get("url", "").strip()
            name   = body.get("name", "").strip()
            email  = body.get("email", "").strip()

            if not url or not name or not email:
                return self._json(400, {"error": "Missing url, name or email"})

            job_id = str(uuid.uuid4())
            store_set(job_id, {"status": "running", "name": name, "email": email})

            self._json(200, {"job_id": job_id})

        except Exception as e:
            self._json(500, {"error": str(e)})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *a): pass
