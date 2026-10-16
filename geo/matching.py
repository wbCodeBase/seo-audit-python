"""
Entity matching: does a tracked domain/brand appear in the AI's results, and
is it *named* in the written answer?

Two different questions:
  - find_entity():       is this domain/brand in the rankings list (search
                         results + citations)? Drives Rank / Score / Cited.
  - mention_positions(): is the entity named in the answer TEXT, and in what
                         order relative to the other tracked entities? This
                         is the "mention" the Qwairy-style metrics count — an
                         AI can recommend "SaasWorx" without linking it.

find_entity() is stricter than the original _find_entity in
api/scheduled_audit.py, which matched raw substrings both ways (so a short
stem like "ai" matched nearly every result, and "x.com" matched
"netflix.com"). Here domains must match on whole labels, and the fuzzy
title match only runs after exact-domain and same-name-domain passes fail.
"""
import re

_MIN_STEM = 4  # shorter stems ("ai", "abc") are too ambiguous to match on their own

_URL_RE = re.compile(r'https?://\S+')
_MD_LINK_TARGET_RE = re.compile(r'\]\((https?://[^)\s]+)\)')


def clean_domain(value):
    v = re.sub(r'^https?://', '', (value or "").strip(), flags=re.I)
    v = re.sub(r'^www\.', '', v, flags=re.I)
    return v.split('/')[0].rstrip('.').lower()


def domain_stem(domain):
    """The brand-ish label of a domain: 'saasworx.ai' → 'saasworx',
    'blog.inoday.co.in' → 'inoday'. A heuristic (no public-suffix list), but
    good enough for brand domains."""
    labels = clean_domain(domain).split('.')
    if len(labels) <= 1:
        return labels[0] if labels else ""
    # drop common second-level suffixes like co.in / com.au
    if len(labels) >= 3 and labels[-2] in ("co", "com", "net", "org", "gov", "ac"):
        return labels[-3]
    return labels[-2]


def _labels(domain):
    return [p for p in re.split(r'[.\-]', domain) if p]


def _same_site(a, b):
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _word_in(needle, haystack):
    return re.search(r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', haystack) is not None


def find_entity(rankings, domain=None, brand=None):
    """Returns the matching rankings item (rank, cited, answer_position,
    title, url) or None. Earlier items win within each pass, so the best
    rank is reported."""
    if not rankings:
        return None

    if domain:
        clean = clean_domain(domain)
        stem = domain_stem(clean)
        for item in rankings:
            if _same_site(item.get("domain", "").lower(), clean):
                return item
        if len(stem) >= _MIN_STEM:
            for item in rankings:
                if stem in _labels(item.get("domain", "").lower()):
                    return item
            for item in rankings:
                if _word_in(stem, item.get("title", "").lower()):
                    return item

    if brand:
        needle = brand.strip().lower()
        compact = re.sub(r'[^a-z0-9]', '', needle)
        if needle:
            for item in rankings:
                d = item.get("domain", "").lower()
                if _word_in(needle, item.get("title", "").lower()) or (
                    len(compact) >= _MIN_STEM and compact in _labels(d)
                ):
                    return item
    return None


def entity_aliases(name=None, domain=None, extra=()):
    """Strings that count as 'naming' an entity in answer text."""
    out = []
    for a in [name, *extra]:
        if a and a.strip():
            out.append(a.strip())
    if domain:
        d = clean_domain(domain)
        out.append(d)
        stem = domain_stem(d)
        if len(stem) >= _MIN_STEM:
            out.append(stem)
    seen, uniq = set(), []
    for a in out:
        k = a.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq


def strip_urls(text):
    """Remove link targets so a brand inside some other site's URL (e.g.
    reddit.com/r/saasworx) isn't counted as a mention. Markdown link TEXT is
    kept — '[SaasWorx](https://…)' still names SaasWorx."""
    text = _MD_LINK_TARGET_RE.sub(']', text or "")
    return _URL_RE.sub(' ', text)


def first_mention_offset(text, aliases):
    """Character offset of the earliest alias match in text (case-insensitive,
    whole-word), or None."""
    low = (text or "").lower()
    best = None
    for alias in aliases:
        a = alias.lower()
        if not a:
            continue
        m = re.search(r'(?<![a-z0-9])' + re.escape(a) + r'(?![a-z0-9])', low)
        if m and (best is None or m.start() < best):
            best = m.start()
    return best


def mention_positions(text, entities, groups=None):
    """entities: {entity_key: [aliases]}. groups (optional): {entity_key:
    group_key} — entities in the same group (e.g. our domain and our brand
    name) share one position slot, so naming ourselves twice doesn't push
    competitors down a place.

    Returns {entity_key: {"mentioned": bool, "offset": int|None,
    "position": int|None}} where position is 1 for the first-named group."""
    text = strip_urls(text)
    groups = groups or {}
    offsets = {k: first_mention_offset(text, aliases) for k, aliases in entities.items()}

    group_offset = {}
    for k, off in offsets.items():
        if off is None:
            continue
        g = groups.get(k, k)
        if g not in group_offset or off < group_offset[g]:
            group_offset[g] = off
    group_pos = {g: i + 1 for i, (g, _) in enumerate(sorted(group_offset.items(), key=lambda kv: kv[1]))}

    out = {}
    for k, off in offsets.items():
        out[k] = {
            "mentioned": off is not None,
            "offset":    off,
            "position":  group_pos.get(groups.get(k, k)) if off is not None else None,
        }
    return out
