"""
What answers cost, and keeping each client inside its monthly budget.

Observed engines: the provider reports the billed amount per call — exact.
API engines: estimated from the usage the API returns:
    input_tokens × in-price + output_tokens × out-price + searches × search fee
Prices are USD per million tokens, matched on the model-name prefix. Set
MODEL_PRICES='{"claude-sonnet": [3, 15], "gpt-5": [1.25, 10]}' to match your
bill; the defaults are deliberately on the high side so budgets aren't
overrun. Search fee: SEARCH_FEE_PER_1K (default 10 = $10 per 1,000 searches).

Budgets: clients.monthly_budget_usd (NULL = no limit). Month = calendar month
in IST. plan_within_budget() trims a run's tasks so the estimated spend never
crosses the remaining budget — observed (real-user) tasks are kept first,
because they are both cheaper and more accurate.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from geo.engines import cost_estimate, source_of

IST = timezone(timedelta(hours=5, minutes=30))

_DEFAULT_PRICES = {          # prefix → [input $/M, output $/M]
    "claude-opus": [15, 75],
    "claude-sonnet": [3, 15],
    "claude-haiku": [1, 5],
    "gpt-5": [1.25, 10],
    "gpt-4.1": [2, 8],
    "gpt-4o": [2.5, 10],
}
_FALLBACK = [5, 15]


def _prices():
    try:
        extra = json.loads(os.environ.get("MODEL_PRICES", "") or "{}")
    except ValueError:
        extra = {}
    merged = dict(_DEFAULT_PRICES)
    merged.update({k: v for k, v in extra.items() if isinstance(v, list) and len(v) == 2})
    return merged


def _price_for(model):
    model = (model or "").lower()
    best = None
    for prefix, p in _prices().items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, p)
    return best[1] if best else _FALLBACK


def _tokens_cost(model, usage):
    pin, pout = _price_for(model)
    return ((usage.get("input_tokens") or 0) * pin + (usage.get("output_tokens") or 0) * pout) / 1e6


def api_cost(model, usage):
    """USD estimate for one API answer (tokens + web searches) from its usage dict."""
    usage = usage or {}
    cost = _tokens_cost(model, usage)
    try:
        fee = float(os.environ.get("SEARCH_FEE_PER_1K", "") or 10)
    except ValueError:
        fee = 10.0
    searches = usage.get("web_search_requests")
    if searches is None and usage.get("input_tokens"):
        searches = 1          # OpenAI doesn't report a count; at least one search ran
    cost += (searches or 0) * fee / 1000
    return round(cost, 6)


def discovery_cost(usage):
    """The cheap brand-extraction pass that runs after an answer, if it did."""
    disc = (usage or {}).get("discovery")
    if not isinstance(disc, dict):
        return 0.0
    return round(_tokens_cost(disc.get("model") or "claude-haiku", disc), 6)


def answer_cost(source, model, usage, provider_cost=None):
    """Total USD for one saved answer."""
    base = provider_cost if source == "observed" and provider_cost is not None else (
        api_cost(model, usage) if source == "api" else 0.0)
    return round(float(base or 0) + discovery_cost(usage), 6)


def month_start_utc(now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def month_spend(session, client_id, now=None):
    from geo.models import Answer
    total = session.scalar(select(func.coalesce(func.sum(Answer.cost_usd), 0.0)).where(
        Answer.client_id == client_id, Answer.created_at >= month_start_utc(now)))
    return float(total or 0)


def plan_within_budget(tasks, budget, spent):
    """Split tasks into (kept, dropped) so spent + estimates <= budget.
    budget None → keep everything. Observed tasks are kept first."""
    if budget is None:
        return list(tasks), []
    remaining = float(budget) - float(spent)
    ordered = sorted(enumerate(tasks), key=lambda it: (source_of(it[1]["app"]) != "observed", it[0]))
    kept_idx, dropped = set(), []
    for i, t in ordered:
        est = cost_estimate(t["app"])
        if est <= remaining:
            remaining -= est
            kept_idx.add(i)
        else:
            dropped.append(t)
    return [t for i, t in enumerate(tasks) if i in kept_idx], dropped
