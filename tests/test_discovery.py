from geo.discovery import KnownEntities, candidate_key, discover, is_generic_source


def _known():
    k = KnownEntities()
    k.add(name="SaasWorx", domain="saasworx.ai")
    k.add(domain="inoday.com")
    return k


def test_generic_sources():
    assert is_generic_source("www.reddit.com")
    assert is_generic_source("en.wikipedia.org")
    assert not is_generic_source("inoday.com")


def test_discover_from_llm_brands():
    brands = [
        {"name": "SaasWorx", "domain": ""},             # self
        {"name": "Inoday", "domain": ""},               # sheet competitor, by stem
        {"name": "Jobin & Jismi", "domain": ""},        # new; domain filled from citation stem? no
        {"name": "LinkedERP", "domain": ""},            # new; domain filled from citation
        {"name": "G2", "domain": "g2.com"},             # a review site, never a competitor
        {"name": "Linked ERP", "domain": ""},           # duplicate of LinkedERP by normalised name
        {"name": "  ", "domain": ""},
    ]
    citations = [{"domain": "linkederp.com"}, {"domain": "reddit.com"}]
    out = discover(brands, citations, _known())
    assert out == [
        {"key": "name:jobinjismi", "name": "Jobin & Jismi", "domain": None},
        {"key": "linkederp.com", "name": "LinkedERP", "domain": "linkederp.com"},
    ]


def test_discover_fallback_uses_citations_when_llm_unavailable():
    citations = [{"domain": "inoday.com"}, {"domain": "reddit.com"}, {"domain": "jobinandjismi.com"},
                 {"domain": "jobinandjismi.com"}, {"domain": "blog.saasworx.ai"}]
    out = discover(None, citations, _known())
    assert out == [{"key": "jobinandjismi.com", "name": "jobinandjismi", "domain": "jobinandjismi.com"}]


def test_known_entities_matching():
    k = _known()
    assert k.knows(domain="www.saasworx.ai")
    assert k.knows(domain="saasworx.com")      # same stem, other TLD
    assert k.knows(name="Saas Worx")
    assert not k.knows(name="LinkedERP", domain="linkederp.com")
    assert candidate_key("X", "https://www.X.com/") == "x.com"
    assert candidate_key("Big Co.", "") == "name:bigco"
