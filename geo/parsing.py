"""
Turns raw Claude / OpenAI web-search responses into a provider-neutral
ParsedAnswer.

The rankings logic (Rank = order in the raw search results the tool call
returned; answer_position = order first cited in the written answer) is the
same as the original _extract_rankings_claude / _extract_rankings_openai in
api/scheduled_audit.py — only the extra outputs are new:
  - answer_text: the full written answer
  - citations:   every distinct URL cited in the answer, in reading order,
                 with a 1-based position
"""
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class ParsedAnswer:
    rankings: list = field(default_factory=list)   # [{domain,title,url,rank,cited,answer_position}]
    grounded: bool = False                        # did the AI actually search the web
    answer_text: str = ""
    citations: list = field(default_factory=list)  # [{position,url,domain,title}]


def domain_from_url(url):
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return re.sub(r'^www\.', '', netloc)


class _Collector:
    """Shared bookkeeping for both providers: search-result order, cited
    domains in first-cited order, and cited URLs in first-cited order."""

    def __init__(self):
        self.seen = {}
        self.order = []
        self.cited_domains = set()
        self.cited_order = []
        self.cited_urls = {}
        self.searched = False

    def add_result(self, url, title):
        domain = domain_from_url(url) if url else ""
        if not domain or domain in self.seen:
            return
        self.seen[domain] = {"domain": domain, "title": title or domain, "url": url}
        self.order.append(domain)

    def add_citation(self, url, title):
        domain = domain_from_url(url) if url else ""
        if not domain:
            return
        if domain not in self.cited_domains:
            self.cited_domains.add(domain)
            self.cited_order.append(domain)
        if url not in self.cited_urls:
            self.cited_urls[url] = {
                "position": len(self.cited_urls) + 1,
                "url":      url,
                "domain":   domain,
                "title":    title or "",
            }
        if domain not in self.seen:
            self.seen[domain] = {"domain": domain, "title": title or domain, "url": url}
            self.order.append(domain)

    def rankings(self):
        answer_pos = {d: i + 1 for i, d in enumerate(self.cited_order)}
        out = []
        for i, domain in enumerate(self.order):
            item = self.seen[domain]
            item["rank"] = i + 1
            item["cited"] = domain in self.cited_domains
            item["answer_position"] = answer_pos.get(domain)
            out.append(item)
        return out

    def citations(self):
        return list(self.cited_urls.values())


def parse_claude(content_blocks):
    """Ranks from the actual web_search_tool_result data Claude's backend
    returned (the real search results), not just from inline citations;
    citations on text blocks set 'cited' and answer_position."""
    c = _Collector()
    text_parts = []

    for block in content_blocks:
        btype = getattr(block, "type", None)

        if btype == "server_tool_use":
            c.searched = True

        elif btype == "web_search_tool_result":
            c.searched = True
            content = getattr(block, "content", None) or []
            # A failed search returns an error object here, not a list.
            if not isinstance(content, list):
                continue
            for result in content:
                url = getattr(result, "url", None)
                if url:
                    c.add_result(url, getattr(result, "title", ""))

        elif btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
            for cit in (getattr(block, "citations", None) or []):
                if getattr(cit, "type", None) != "web_search_result_location":
                    continue
                c.add_citation(getattr(cit, "url", None), getattr(cit, "title", ""))

    return ParsedAnswer(
        rankings=c.rankings(), grounded=c.searched,
        answer_text="".join(text_parts), citations=c.citations(),
    )


def parse_openai(output_items, output_text=None):
    """Responses API shape: raw ranked results come from each
    web_search_call item's action.sources[] (URL only, no title); inline
    citations come from url_citation annotations on output_text content,
    sorted by start_index so they're in true reading order."""
    c = _Collector()
    text_parts = []

    for item in output_items:
        itype = getattr(item, "type", None)

        if itype == "web_search_call":
            c.searched = True
            action = getattr(item, "action", None)
            for src in (getattr(action, "sources", None) or []):
                url = getattr(src, "url", None)
                if url:
                    c.add_result(url, None)

        elif itype == "message":
            for content in (getattr(item, "content", None) or []):
                if getattr(content, "type", None) != "output_text":
                    continue
                text_parts.append(getattr(content, "text", "") or "")
                annotations = [
                    a for a in (getattr(content, "annotations", None) or [])
                    if getattr(a, "type", None) == "url_citation"
                ]
                annotations.sort(key=lambda a: getattr(a, "start_index", 0) or 0)
                for ann in annotations:
                    c.add_citation(getattr(ann, "url", None), getattr(ann, "title", ""))

    return ParsedAnswer(
        rankings=c.rankings(), grounded=c.searched,
        answer_text=output_text if output_text is not None else "".join(text_parts),
        citations=c.citations(),
    )
