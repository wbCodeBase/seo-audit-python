from geo.matching import (
    clean_domain, domain_stem, entity_aliases, find_entity, mention_positions, strip_urls,
)


def R(*items):
    return [{"domain": d, "title": t, "rank": i + 1, "cited": False, "answer_position": None}
            for i, (d, t) in enumerate(items)]


def test_clean_domain_and_stem():
    assert clean_domain("https://www.SaasWorx.ai/about") == "saasworx.ai"
    assert domain_stem("saasworx.ai") == "saasworx"
    assert domain_stem("blog.inoday.co.in") == "inoday"
    assert domain_stem("inoday.com") == "inoday"


def test_find_entity_exact_and_subdomain():
    rk = R(("g2.com", "G2"), ("blog.saasworx.ai", "Blog"), ("saasworx.ai", "Home"))
    assert find_entity(rk, domain="saasworx.ai")["rank"] == 2   # subdomain counts, best rank wins
    assert find_entity(rk, domain="https://www.saasworx.ai/") ["rank"] == 2


def test_find_entity_same_name_other_tld():
    rk = R(("g2.com", "G2"), ("saasworx.com", "SaasWorx US"))
    assert find_entity(rk, domain="saasworx.ai")["rank"] == 2


def test_exact_domain_beats_earlier_title_mention():
    rk = R(("reddit.com", "Is SaasWorx any good?"), ("saasworx.ai", "SaasWorx"))
    assert find_entity(rk, domain="saasworx.ai")["rank"] == 2


def test_no_substring_false_positives():
    # old matcher: "x.com" in "netflix.com" → match; short stems matched everything
    rk = R(("netflix.com", "Netflix"), ("openai.com", "OpenAI"))
    assert find_entity(rk, domain="x.com") is None
    assert find_entity(rk, domain="ai.com") is None


def test_title_fallback_whole_word():
    rk = R(("g2.com", "Inoday reviews 2026"), ("other.com", "Inodayx"))
    assert find_entity(rk, domain="inoday.com")["rank"] == 1
    assert find_entity(R(("other.com", "Inodayx")), domain="inoday.com") is None


def test_find_entity_by_brand():
    rk = R(("g2.com", "G2"), ("saasworx.ai", "Home"), ("x.io", "Why Saas Worx wins"))
    assert find_entity(rk, brand="SaasWorx")["rank"] == 2          # compact brand == domain label
    assert find_entity(rk, brand="Saas Worx")["rank"] == 2
    assert find_entity(R(("x.io", "Why Saas Worx wins")), brand="Saas Worx")["rank"] == 1
    assert find_entity(rk, brand="Nobody") is None
    assert find_entity([], domain="a.com") is None


def test_entity_aliases():
    assert entity_aliases(name="SaasWorx", domain="https://www.saasworx.ai") == ["SaasWorx", "saasworx.ai"]
    assert entity_aliases(domain="inoday.com") == ["inoday.com", "inoday"]
    assert entity_aliases(domain="abc.com") == ["abc.com"]           # stem too short to use alone


def test_strip_urls_keeps_link_text():
    t = "Try [SaasWorx](https://saasworx.ai/x) or see https://reddit.com/r/inoday today"
    out = strip_urls(t)
    assert "SaasWorx" in out and "saasworx.ai/x" not in out and "inoday" not in out


def test_mention_positions_with_groups():
    text = ("For NetSuite in Noida, Inoday is popular. SaasWorx (saasworx.ai) is newer. "
            "LinkedERP also helps. See https://reddit.com/r/jobinandjismi.")
    ents = {
        "own":       ["saasworx.ai", "saasworx"],
        "own brand": ["SaasWorx"],
        "inoday":    ["inoday.com", "inoday"],
        "linkederp": ["linkederp.com", "linkederp"],
        "jj":        ["jobinandjismi.com", "jobinandjismi"],
    }
    groups = {"own": "self", "own brand": "self"}
    m = mention_positions(text, ents, groups)
    assert m["inoday"] == {"mentioned": True, "offset": text.lower().index("inoday"), "position": 1}
    assert m["own"]["position"] == 2 and m["own brand"]["position"] == 2   # one slot for self
    assert m["linkederp"]["position"] == 3                                   # not pushed to 4
    assert m["jj"] == {"mentioned": False, "offset": None, "position": None}  # only inside a URL


def test_mention_whole_word_only():
    m = mention_positions("Inodayx and Pre-SaasWorx", {"a": ["inoday"], "b": ["saasworx"]})
    assert m["a"]["mentioned"] is False
    assert m["b"]["mentioned"] is True   # hyphen is a word boundary
