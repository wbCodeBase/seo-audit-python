"""
Rank → score rule and keyword intent classification.

Both are copied verbatim from api/scheduled_audit.py (and ai_rank_run.py) —
historical rows in the Results tabs were scored with exactly this rule, so it
must not change or old and new data stop being comparable. The Phase 4
"position score" metric reuses visibility_score() as-is.
"""

_LOCAL_HINTS = (
    "near me", "noida", "delhi", "mumbai", "bangalore", "bengaluru", "chennai",
    "hyderabad", "pune", "gurgaon", "gurugram", "kolkata", "ahmedabad", "jaipur",
    "in india", "in usa", "in uk", "in canada", "in dubai", "in uae",
)
_TRANSACTIONAL_HINTS = (
    "partners", "partner", "hire", "agency", "agencies", "consultant", "consultants",
    "company", "companies", "provider", "providers", "services", "service",
    "implementation", "pricing", "price", "cost", "quote", "buy", "vendor", "vendors",
)
_COMPARISON_HINTS = ("vs", "versus", "best", "top", "compare", "comparison", "alternative", "alternatives")
_INFORMATIONAL_HINTS = ("what is", "what are", "how to", "how does", "why", "guide", "tips", "meaning")


def classify_intent(query):
    q = f" {query.strip().lower()} "
    if any(h in q for h in _LOCAL_HINTS):
        return "local"
    if any(h in q for h in _INFORMATIONAL_HINTS):
        return "informational"
    if any(h in q for h in _COMPARISON_HINTS):
        return "comparison"
    if any(h in q for h in _TRANSACTIONAL_HINTS):
        return "transactional"
    return "informational"


def visibility_score(rank, grounded, cited):
    """None when the AI never searched (nothing to rank against), 0 when not
    found, otherwise 100 for #1 dropping 12 per position (floor 5), +10 if
    the AI also cited the entity in its written answer (cap 100)."""
    if not grounded:
        return None
    if not rank:
        return 0
    score = max(5, 100 - (rank - 1) * 12)
    if cited:
        score = min(100, score + 10)
    return score
