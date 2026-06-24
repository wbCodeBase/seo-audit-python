"""
/api/ai_rank — AI Platform Ranking Checker
Queries AI platforms with a user prompt to check if a website/brand appears in responses.
Currently active: Claude (Anthropic) — requires ANTHROPIC_API_KEY
Coming soon: ChatGPT (OpenAI), Gemini (Google), Perplexity
"""

import json, os, re
from http.server import BaseHTTPRequestHandler

# Try the newer model first, fall back to the stable GA model
CLAUDE_MODELS = [
    "claude-sonnet-4-5",
    "claude-3-5-sonnet-20241022",
]


def query_claude(prompt, api_key):
    """
    Returns (response_text, error_message).
    Tries each model in CLAUDE_MODELS until one works.
    """
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    last_err = ""
    for model in CLAUDE_MODELS:
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}]
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            return text, None
        except anthropic.NotFoundError:
            last_err = f"Model '{model}' not found, trying next..."
            continue
        except anthropic.AuthenticationError:
            return None, "Invalid API key — check your ANTHROPIC_API_KEY environment variable."
        except anthropic.RateLimitError:
            return None, "Rate limit reached — try again in a few seconds."
        except anthropic.APITimeoutError:
            return None, "Claude API timed out — try a shorter prompt or try again."
        except Exception as e:
            return None, f"Claude API error: {type(e).__name__}: {str(e)}"

    return None, f"No working model found. Last error: {last_err}"


def detect_mentions(response_text, domain):
    """Return (mentioned, count) for how many times domain/brand appears."""
    if not domain or not response_text:
        return False, 0

    clean = re.sub(r'^https?://', '', domain, flags=re.I)
    clean = re.sub(r'^www\.', '', clean, flags=re.I)
    clean = clean.rstrip('/')

    brand = re.split(r'\.', clean)[0] if '.' in clean else clean

    patterns = list(dict.fromkeys([clean, brand]))  # deduplicate, preserve order
    count = 0
    for pat in patterns:
        count += len(re.findall(re.escape(pat), response_text, re.I))

    return count > 0, count


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

            results = {}

            # ── Claude (Anthropic) ────────────────────────────────────────────
            anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if anthropic_key:
                response_text, err = query_claude(prompt, anthropic_key)
                if response_text:
                    mentioned, count = detect_mentions(response_text, domain)
                    results["claude"] = {
                        "status":        "success",
                        "response":      response_text,
                        "mentioned":     mentioned,
                        "mention_count": count,
                        "platform":      "Claude",
                        "provider":      "Anthropic",
                        "model":         "Claude Sonnet",
                    }
                else:
                    results["claude"] = {
                        "status":        "error",
                        "error_detail":  err or "Unknown error",
                        "response":      None,
                        "mentioned":     False,
                        "mention_count": 0,
                        "platform":      "Claude",
                        "provider":      "Anthropic",
                        "model":         "Claude Sonnet",
                    }
            else:
                results["claude"] = {
                    "status":        "no_key",
                    "error_detail":  "ANTHROPIC_API_KEY is not set in environment variables.",
                    "response":      None,
                    "mentioned":     False,
                    "mention_count": 0,
                    "platform":      "Claude",
                    "provider":      "Anthropic",
                    "model":         "Claude Sonnet",
                }

            # ── ChatGPT (OpenAI) ──────────────────────────────────────────────
            results["chatgpt"] = {
                "status": "coming_soon", "response": None,
                "mentioned": False, "mention_count": 0,
                "platform": "ChatGPT", "provider": "OpenAI", "model": "GPT-4o",
            }

            # ── Gemini (Google) ───────────────────────────────────────────────
            results["gemini"] = {
                "status": "coming_soon", "response": None,
                "mentioned": False, "mention_count": 0,
                "platform": "Gemini", "provider": "Google", "model": "Gemini 1.5 Pro",
            }

            # ── Perplexity ────────────────────────────────────────────────────
            results["perplexity"] = {
                "status": "coming_soon", "response": None,
                "mentioned": False, "mention_count": 0,
                "platform": "Perplexity", "provider": "Perplexity AI", "model": "Sonar",
            }

            self._json(200, {"ok": True, "results": results})

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
