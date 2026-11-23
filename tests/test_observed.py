"""Observed (real-user) engines: DataForSEO response parsing and error mapping.
Fixtures follow the documented response shapes; no network is used."""
import io
import json
import urllib.error

import pytest

from geo import observed
from geo.config import get_settings
from geo.providers import available_providers, ask_observed
from dataclasses import replace


def _envelope(result, cost=0.004, code=20000, task_code=20000):
    return {"status_code": code, "status_message": "Ok.", "cost": cost, "tasks": [
        {"status_code": task_code, "status_message": "Ok.", "cost": cost, "result": [result] if result else None}]}


CHATGPT_RESULT = {
    "keyword": "best netsuite partner in india",
    "model": "gpt-5-2",
    "markdown": "Top picks: **Inoday** is popular. **SaasWorx** is also strong.",
    "search_results": [
        {"type": "chatgpt_search_result", "url": "https://inoday.com/netsuite", "domain": "inoday.com", "title": "Inoday"},
        {"type": "chatgpt_search_result", "url": "https://www.saasworx.ai/", "domain": "saasworx.ai", "title": "SaasWorx"},
        {"type": "chatgpt_search_result", "url": "https://unused.com/x", "domain": "unused.com", "title": "Unused"},
    ],
    "sources": [
        {"type": "chat_gpt_source", "url": "https://www.saasworx.ai/", "domain": "saasworx.ai", "title": "SaasWorx"},
        {"type": "chat_gpt_source", "url": "https://clutch.co/netsuite", "domain": "clutch.co", "title": "Clutch list"},
    ],
    "fan_out_queries": ["netsuite partners india 2026"],
    "brand_entities": [
        {"type": "chat_gpt_brand_entity", "title": "Inoday", "urls": [{"url": "https://inoday.com", "domain": "inoday.com"}]},
        {"type": "chat_gpt_brand_entity", "title": "SaasWorx", "urls": []},
        {"type": "chat_gpt_brand_entity", "title": "inoday", "urls": []},
    ],
    "items": [],
}

AIO_RESULT = {
    "keyword": "netsuite partner",
    "items": [
        {"type": "organic", "url": "https://x.com"},
        {"type": "ai_overview", "markdown": "SaasWorx and Inoday are NetSuite partners.",
         "items": [{"type": "ai_overview_element", "text": "...",
                    "references": [{"type": "ai_overview_reference", "url": "https://inoday.com/a", "domain": "inoday.com", "title": "Inoday"}],
                    "links": None}],
         "references": [{"type": "ai_overview_reference", "url": "https://saasworx.ai/p", "domain": "saasworx.ai",
                         "title": "SaasWorx", "source": "SaasWorx"}]},
    ],
}


def test_parse_chatgpt_real_answer():
    parsed, brands = observed.parse_llm_scraper(CHATGPT_RESULT)
    assert parsed.answer_text.startswith("Top picks")
    assert parsed.grounded is True
    # rank = retrieval order; cited = actually used in the answer
    by = {r["domain"]: r for r in parsed.rankings}
    assert [r["domain"] for r in parsed.rankings] == ["inoday.com", "saasworx.ai", "unused.com", "clutch.co"]
    assert by["saasworx.ai"]["cited"] and by["saasworx.ai"]["answer_position"] == 1
    assert not by["inoday.com"]["cited"]
    assert [c["domain"] for c in parsed.citations] == ["saasworx.ai", "clutch.co"]
    assert brands == [{"name": "Inoday", "domain": "inoday.com"}, {"name": "SaasWorx", "domain": ""}]


def test_parse_chatgpt_falls_back_to_items():
    res = {"items": [{"type": "chat_gpt_text", "markdown": "Hello", "sources": [
        {"url": "https://a.com/1", "domain": "a.com", "title": "A"}]}]}
    parsed, brands = observed.parse_llm_scraper(res)
    assert parsed.answer_text == "Hello" and parsed.citations[0]["domain"] == "a.com" and brands is None


def test_parse_ai_overview():
    parsed, brands, shown = observed.parse_overview(AIO_RESULT)
    assert shown and brands is None
    assert parsed.answer_text.startswith("SaasWorx")
    assert [r["domain"] for r in parsed.rankings] == ["saasworx.ai", "inoday.com"]
    assert all(r["cited"] for r in parsed.rankings)


def test_no_ai_overview_is_a_result_not_an_error():
    parsed, _b, shown = observed.parse_overview({"items": [{"type": "organic"}]})
    assert shown is False and parsed.answer_text == "" and parsed.rankings == []


@pytest.mark.parametrize("body,msg", [
    (_envelope(None, code=40100), "rejected the login"),
    (_envelope(None, code=40200), "balance is empty"),
    (_envelope(None, task_code=40210), "balance is empty"),
    (_envelope(None, task_code=40501), "task error 40501"),
    ({"status_code": 20000, "tasks": []}, "no task"),
])
def test_unwrap_errors(body, msg):
    with pytest.raises(observed.ObservedError, match=msg):
        observed.unwrap(body)


def test_build_task_and_location():
    t = observed.build_task("google_aio", "q" * 900, 2356)
    assert len(t["keyword"]) == 700 and t["load_async_ai_overview"] is True and t["location_code"] == 2356
    assert observed.build_task("chatgpt_web", "q", 2840)["force_web_search"] is True
    assert observed.location_code("IN", 2840) == 2356
    assert observed.location_code(None, 2840) == 2840
    assert observed.location_code("zz", 2826) == 2826


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_observe_end_to_end_with_fake_http():
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        seen["auth"] = req.headers["Authorization"]
        return _Resp(json.dumps(_envelope(CHATGPT_RESULT, cost=0.004)).encode())

    parsed, brands, cost, info = observed.observe("chatgpt_web", "best partner", "me", "pw", 2356, opener=opener)
    assert seen["url"].endswith("/ai_optimization/chat_gpt/llm_scraper/live/advanced")
    assert seen["body"][0]["keyword"] == "best partner" and seen["auth"].startswith("Basic ")
    assert cost == 0.004 and info["model"] == "gpt-5-2" and info["fan_out_queries"]
    assert brands and parsed.citations


def test_http_errors_are_friendly():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 402, "Payment", {}, None)
    with pytest.raises(observed.ObservedError, match="balance"):
        observed.observe("google_aio", "q", "a", "b", 2840, opener=opener)


def test_registry_includes_observed_only_with_credentials(monkeypatch):
    base = replace(get_settings(), anthropic_api_key="", openai_api_key="",
                   dataforseo_login="", dataforseo_password="", observed_engines=())
    assert available_providers(base) == {}
    on = replace(base, dataforseo_login="me", dataforseo_password="pw")
    provs = available_providers(on)
    assert set(provs) == {"chatgpt_web", "gemini_web", "google_aio", "google_ai_mode"}
    assert all(p["source"] == "observed" for p in provs.values())
    only = replace(on, observed_engines=("google_aio",))
    assert set(available_providers(only)) == {"google_aio"}


def test_ask_observed_maps_errors(monkeypatch):
    def boom(*a, **k):
        raise observed.ObservedError("DataForSEO account balance is empty")
    monkeypatch.setattr(observed, "observe", boom)
    st = replace(get_settings(), dataforseo_login="me", dataforseo_password="pw")
    r = ask_observed("chatgpt_web", "q", st, country="in")
    assert r.error.startswith("DataForSEO account balance")
