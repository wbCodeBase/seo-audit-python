"""
Self-hosted entry point: serves every api/*.py endpoint and the public/ UI
from one long-running process, so the Python side runs on a VPS (or any
Docker host) exactly as it does on Vercel, without serverless time limits.

  python server.py                 # listens on 0.0.0.0:8000 (PORT / HOST env to change)

Each api/<name>.py keeps its Vercel-style `handler` class unchanged. The
router below picks the module from the URL path (/api/<name>), switches the
request object to that module's handler class and calls its do_<METHOD>, so
every helper method the handler defines (e.g. manual_run's _json) works.
Anything else is served from public/ (/ → public/index.html).
"""
import importlib
import mimetypes
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geo.config import load_dotenv  # noqa: E402  (after sys.path is set)

# Only modules that exist in api/ are routable; names are checked against this
# list, never imported straight from the URL.
API_MODULES = sorted(p.stem for p in (ROOT / "api").glob("*.py") if not p.stem.startswith("_"))
_NAME = re.compile(r"^/api/([a-z0-9_]+)/?$")
_cache = {}


def _handler_class(name):
    if name not in _cache:
        _cache[name] = importlib.import_module(f"api.{name}").handler
    return _cache[name]


class Router(BaseHTTPRequestHandler):
    server_version = "geo-server"
    protocol_version = "HTTP/1.0"   # same as the api handlers expect (they don't chunk)

    def _dispatch(self, method):
        path = urlsplit(self.path).path
        m = _NAME.match(path)
        if m:
            name = m.group(1)
            if name not in API_MODULES:
                return self._plain(404, "Not found")
            try:
                cls = _handler_class(name)
            except Exception as e:  # import error in one endpoint shouldn't take the server down
                return self._plain(500, f"Could not load api/{name}.py: {type(e).__name__}: {e}")
            fn = getattr(cls, f"do_{method}", None)
            if fn is None:
                return self._plain(405, "Method not allowed")
            self.__class__ = cls      # same base class; lets the handler use its own helpers
            return fn(self)
        if method in ("GET", "HEAD"):
            return self._static(path, head=(method == "HEAD"))
        return self._plain(404, "Not found")

    def _static(self, path, head=False):
        rel = unquote(path).lstrip("/") or "index.html"
        target = (PUBLIC / rel).resolve()
        if target.is_dir():
            target = target / "index.html"
        if PUBLIC.resolve() not in target.parents or not target.is_file():
            return self._plain(404, "Not found")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def _plain(self, code, text):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self): self._dispatch("GET")
    def do_POST(self): self._dispatch("POST")
    def do_OPTIONS(self): self._dispatch("OPTIONS")
    def do_HEAD(self): self._dispatch("HEAD")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    load_dotenv()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer((host, port), Router)
    httpd.daemon_threads = True
    print(f"geo server on http://{host}:{port}  (api: {', '.join(API_MODULES)})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
