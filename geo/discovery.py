"""
New-competitor discovery.

Cited domains are mostly *sources* (Reddit, G2, Wikipedia, news sites), not
competitors, so the primary signal is a cheap LLM pass over the answer text
that lists the companies/products the AI actually presented as options.
If that call is disabled or fails, fall back to cited domains minus a list
of well-known publisher/directory sites.

Anything found that isn't already a known entity (self, a sheet competitor,
or a previously reviewed one — including ones marked 'ignored') becomes a
candidate with relationship 'pending' for a human to review.
"""
import json
import re

from geo.matching import clean_domain, domain_stem

# Publishers, forums, directories and platforms that show up as citations
# but are never a client's competitor. Matched on whole-site (subdomains too).
GENERIC_SOURCE_DOMAINS = frozenset({
    "wikipedia.org", "reddit.com", "quora.com", "youtube.com", "medium.com",
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "github.com", "stackoverflow.com", "google.com", "bing.com", "apple.com",
    "g2.com", "capterra.com", "getapp.com", "softwareadvice.com", "trustradius.com",
    "clutch.co", "goodfirms.co", "glassdoor.com", "indeed.com", "crunchbase.com",
    "forbes.com", "techcrunch.com", "businessinsider.com", "zdnet.com", "pcmag.com",
    "gartner.com", "investopedia.com", "nytimes.com", "theverge.com", "wired.com",
    "economictimes.indiatimes.com", "timesofindia.indiatimes.com", "ndtv.com",
    "justdial.com", "sulekha.com", "indiamart.com", "tradeindia.com", "ambitionbox.com",
    "yelp.com", "tripadvisor.com", "trustpilot.com", "amazon.com", "amazon.in",
})

EXTRACT_PROMPT = """\
Below is an AI assistant's answer to the question: "{question}"

We track the business {brand} ({domain}). List every business the answer presents as an alternative a buyer could choose INSTEAD of {brand} — i.e. companies offering the same kind of product or service — in the order each is first named.

Do NOT include: software tools, ad or social media platforms, marketplaces, search engines, review sites, directories, publications, forums, or any source only cited as evidence — unless that company itself sells the same kind of service as {brand}. Do not include {brand} itself.

For each one give its name exactly as written and its website domain if the answer states or links it (otherwise an empty string). Return an empty list if there are none.

<answer>
{answer}
</answer>"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "brands": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":   {"type": "string"},
                    "domain": {"type": "string"},
                },
                "required": ["name", "domain"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["brands"],
    "additionalProperties": False,
}


def is_generic_source(domain):
    d = clean_domain(domain)
    return any(d == g or d.endswith("." + g) for g in GENERIC_SOURCE_DOMAINS)


def extract_brands_llm(answer_text, question, api_key, model, brand="", domain=""):
    """Returns ([{name, domain}], usage_dict) or (None, error_str) when the
    call fails, so callers can fall back to citation-based discovery.
    brand/domain describe the client, so only real alternatives to it come
    back (not the tools and platforms an answer happens to mention)."""
    if not answer_text or not answer_text.strip():
        return [], {}
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _SCHEMA},
            },
            messages=[{"role": "user", "content": EXTRACT_PROMPT.format(
                question=question, answer=answer_text[:60000],
                brand=brand or domain or "the client", domain=domain or "no website given",
            )}],
        )
    except Exception as e:  # never let discovery cost us the answer itself
        return None, f"{type(e).__name__}: {e}"
    if resp.stop_reason in ("refusal", "max_tokens"):
        return None, f"extraction stopped: {resp.stop_reason}"
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        brands = json.loads(text).get("brands", [])
    except (ValueError, AttributeError):
        return None, "extraction returned invalid JSON"
    usage = {
        "model": model,
        "input_tokens": getattr(resp.usage, "input_tokens", None),
        "output_tokens": getattr(resp.usage, "output_tokens", None),
    }
    return [b for b in brands if (b.get("name") or "").strip()], usage


def _compact(name):
    return re.sub(r'[^a-z0-9]', '', (name or "").lower())


def candidate_key(name, domain):
    """Stable identity for a competitor row: its domain when known, else its
    normalised name."""
    d = clean_domain(domain) if domain else ""
    return d if d else "name:" + _compact(name)


class KnownEntities:
    """Everything that should NOT be flagged as new: self, sheet competitors,
    and every previously discovered entity whatever its relationship."""

    def __init__(self):
        self.domains = set()
        self.names = set()

    def add(self, name=None, domain=None):
        if domain:
            d = clean_domain(domain)
            if d:
                self.domains.add(d)
                stem = domain_stem(d)
                if len(stem) >= 4:
                    self.names.add(stem)
        if name and _compact(name):
            self.names.add(_compact(name))

    def knows(self, name=None, domain=None):
        if domain:
            d = clean_domain(domain)
            if any(d == k or d.endswith("." + k) or k.endswith("." + d) for k in self.domains):
                return True
            if domain_stem(d) in self.names:
                return True
        if name and _compact(name) in self.names:
            return True
        return False


def discover(brands, citations, known):
    """brands: LLM output list, or None if extraction was unavailable.
    citations: ParsedAnswer.citations. known: KnownEntities.
    Returns [{key, name, domain}] — de-duplicated, in answer order."""
    out, seen = [], set()

    def push(name, domain):
        domain = clean_domain(domain) if domain else ""
        if domain and is_generic_source(domain):
            return
        if known.knows(name=name, domain=domain):
            return
        key = candidate_key(name, domain)
        if key in seen or key == "name:":
            return
        seen.add(key)
        out.append({"key": key, "name": (name or domain_stem(domain)).strip(), "domain": domain or None})

    if brands is not None:
        cited_by_stem = {domain_stem(c["domain"]): c["domain"] for c in citations}
        for b in brands:
            name, domain = b.get("name", ""), b.get("domain", "")
            # Fill a missing domain from a citation whose stem matches the name.
            if not domain and _compact(name) in cited_by_stem:
                domain = cited_by_stem[_compact(name)]
            push(name, domain)
    else:
        for c in citations:
            push(None, c["domain"])
    return out
