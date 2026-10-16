from types import SimpleNamespace as NS

from geo.parsing import domain_from_url, parse_claude, parse_openai


def test_domain_from_url():
    assert domain_from_url("https://www.SaasWorx.ai/path?q=1") == "saasworx.ai"
    assert domain_from_url("https://blog.inoday.com/x") == "blog.inoday.com"
    assert domain_from_url("not a url") == ""


def _claude_response():
    return [
        NS(type="server_tool_use"),
        NS(type="web_search_tool_result", content=[
            NS(url="https://www.inoday.com/netsuite", title="Inoday NetSuite"),
            NS(url="https://g2.com/erp", title="G2 ERP"),
            NS(url="https://inoday.com/other", title="dup domain, ignored"),
            NS(url="https://saasworx.ai/", title="SaasWorx"),
        ]),
        NS(type="text", text="Top partners: ", citations=None),
        NS(type="text", text="SaasWorx is a strong pick.", citations=[
            NS(type="web_search_result_location", url="https://saasworx.ai/", title="SaasWorx"),
        ]),
        NS(type="text", text=" Also linkederp.", citations=[
            NS(type="web_search_result_location", url="https://linkederp.com/a", title="LinkedERP"),
            NS(type="web_search_result_location", url="https://saasworx.ai/", title="SaasWorx again"),
            NS(type="web_search_result_location", url="https://saasworx.ai/pricing", title="Pricing"),
            NS(type="something_else", url="https://ignored.com"),
        ]),
    ]


def test_parse_claude_rankings_and_new_fields():
    p = parse_claude(_claude_response())
    assert p.grounded is True
    assert [r["domain"] for r in p.rankings] == ["inoday.com", "g2.com", "saasworx.ai", "linkederp.com"]
    assert [r["rank"] for r in p.rankings] == [1, 2, 3, 4]
    by = {r["domain"]: r for r in p.rankings}
    assert by["saasworx.ai"]["cited"] and by["saasworx.ai"]["answer_position"] == 1
    assert by["linkederp.com"]["cited"] and by["linkederp.com"]["answer_position"] == 2
    assert not by["inoday.com"]["cited"] and by["inoday.com"]["answer_position"] is None
    # citation-only domain keeps its citation title
    assert by["linkederp.com"]["title"] == "LinkedERP"

    assert p.answer_text == "Top partners: SaasWorx is a strong pick. Also linkederp."
    assert [(c["position"], c["url"]) for c in p.citations] == [
        (1, "https://saasworx.ai/"),
        (2, "https://linkederp.com/a"),
        (3, "https://saasworx.ai/pricing"),   # distinct URL, same domain → own position
    ]
    assert p.citations[0]["domain"] == "saasworx.ai"


def test_parse_claude_search_error_object_and_no_search():
    p = parse_claude([NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded"))])
    assert p.grounded is True and p.rankings == []

    p = parse_claude([NS(type="text", text="From memory.", citations=None)])
    assert p.grounded is False and p.rankings == [] and p.answer_text == "From memory."


def test_parse_openai_sorts_annotations_by_reading_order():
    output = [
        NS(type="web_search_call", action=NS(sources=[
            NS(url="https://inoday.com/"), NS(url="https://www.saasworx.ai/"), NS(url=None),
        ])),
        NS(type="message", content=[
            NS(type="output_text", text="answer", annotations=[
                NS(type="url_citation", url="https://linkederp.com/", title="LinkedERP", start_index=50),
                NS(type="url_citation", url="https://saasworx.ai/", title="SaasWorx", start_index=10),
                NS(type="file_citation", url="https://nope.com", start_index=1),
            ]),
            NS(type="refusal"),
        ]),
    ]
    p = parse_openai(output, "Full answer text")
    assert p.grounded is True
    assert [r["domain"] for r in p.rankings] == ["inoday.com", "saasworx.ai", "linkederp.com"]
    assert p.rankings[0]["title"] == "inoday.com"   # Responses API gives no titles for sources
    by = {r["domain"]: r for r in p.rankings}
    assert by["saasworx.ai"]["answer_position"] == 1
    assert by["linkederp.com"]["answer_position"] == 2
    assert [c["url"] for c in p.citations] == ["https://saasworx.ai/", "https://linkederp.com/"]
    assert p.answer_text == "Full answer text"


def test_parse_openai_no_search():
    p = parse_openai([NS(type="message", content=[NS(type="output_text", text="hi", annotations=[])])])
    assert p.grounded is False and p.rankings == [] and p.answer_text == "hi"
