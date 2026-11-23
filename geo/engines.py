"""
Every place an answer can come from, in one catalogue.

Two kinds of source:
  api       — we call the AI vendor's developer API with web search on
              (Claude, ChatGPT). Cheap to start, but it is NOT what a person
              sees in the app: different system prompt, search index, location
              and model routing. Treat it as a proxy.
  observed  — the answer a real user sees in the product (chatgpt.com,
              gemini.google.com, Google's AI Overview / AI Mode on the results
              page), captured for us by a SERP/LLM data provider (DataForSEO).
              This is the "ground truth" the dashboard leads with.

Each engine is its own ai_app key, so the metrics roll-up, the matrix and the
competitor share-of-voice work for observed engines with no changes; the
`source` and `family` fields let the web app filter and pair them
(e.g. "ChatGPT · API" vs "ChatGPT · real").

cost_estimate is USD per answer, used only to plan runs against a client's
monthly budget before anything is spent. The answer's real cost is recorded
afterwards in answers.cost_usd (observed: the provider's billed amount;
api: tokens × MODEL_PRICES + search fees — see geo/costs.py). Override the
estimates with ENGINE_COST_ESTIMATES='{"claude": 0.08}'.
"""
import json
import os
from dataclasses import dataclass

SOURCES = ("api", "observed")


@dataclass(frozen=True)
class Engine:
    key: str
    label: str
    source: str          # api | observed
    family: str          # pairs an API engine with its real-user counterpart
    cost_estimate: float


ENGINES = {
    "claude":         Engine("claude",         "Claude · API",          "api",      "claude",  0.08),
    "chatgpt":        Engine("chatgpt",        "ChatGPT · API",         "api",      "chatgpt", 0.04),
    "chatgpt_web":    Engine("chatgpt_web",    "ChatGPT · real",        "observed", "chatgpt", 0.004),
    "gemini_web":     Engine("gemini_web",     "Gemini · real",         "observed", "gemini",  0.004),
    "google_aio":     Engine("google_aio",     "Google AI Overviews",   "observed", "google",  0.0026),
    "google_ai_mode": Engine("google_ai_mode", "Google AI Mode",        "observed", "google_ai_mode", 0.004),
}

API_ENGINES = tuple(k for k, e in ENGINES.items() if e.source == "api")
OBSERVED_ENGINES = tuple(k for k, e in ENGINES.items() if e.source == "observed")


def engine(key):
    return ENGINES.get(key)


def source_of(key):
    e = ENGINES.get(key)
    return e.source if e else "api"


def cost_estimate(key):
    try:
        overrides = json.loads(os.environ.get("ENGINE_COST_ESTIMATES", "") or "{}")
    except ValueError:
        overrides = {}
    if isinstance(overrides.get(key), (int, float)):
        return float(overrides[key])
    e = ENGINES.get(key)
    return e.cost_estimate if e else 0.05
