import pytest

from geo.sheets_io import (
    RESULTS_HEADER, is_active, parse_keyword_rows, parse_question_rows,
    projects_from_keyword_rows, results_row,
)

KNOWN = {"claude": {}, "chatgpt": {}}


@pytest.mark.parametrize("cell", ["", "TRUE", "true", " Yes ", "y", "1", "on", "Active"])
def test_active_values(cell):
    assert is_active(cell)


@pytest.mark.parametrize("cell", ["FALSE", "false", "No", "N", "0", "off", "paused", "inactive", "flase"])
def test_inactive_values_are_skipped(cell):
    assert not is_active(cell)


def test_parse_keyword_rows_skips_inactive_and_reports_them():
    rows = [
        ["SaasWorx", "saasworx.ai", "SaasWorx", "inoday.com, linkederp.com", "netsuite partners noida", "TRUE", ""],
        ["SaasWorx", "saasworx.ai", "SaasWorx", "", "paused keyword", "No"],
        ["", "acme.com", "", "", "acme kw"],                       # short row, blank Active → active
        ["X", "", "", "", "no domain → ignored", "TRUE"],
        ["Y", "y.com", "", "", "chatgpt only", "", "ChatGPT, gemini"],
    ]
    checks, skipped = parse_keyword_rows(rows, KNOWN)
    assert [c["text"] for c in checks] == ["netsuite partners noida", "acme kw", "chatgpt only"]
    assert checks[0]["competitors"] == ["inoday.com", "linkederp.com"]
    assert checks[0]["kind"] == "keyword" and checks[0]["platforms"] == ["claude", "chatgpt"]
    assert checks[1]["project"] == "acme.com"                    # falls back to domain
    assert checks[2]["platforms"] == ["chatgpt"]                 # unknown 'gemini' ignored
    assert skipped == [{"tab": "Keywords", "row": 3, "text": "paused keyword", "active": "No"}]


def test_questions_inherit_client_from_keywords_tab():
    kw = [
        ["SaasWorx", "saasworx.ai", "SaasWorx", "inoday.com", "kw1", "TRUE"],
        ["SaasWorx", "saasworx.ai", "", "linkederp.com, inoday.com", "kw2", "FALSE"],  # paused, still defines client
    ]
    projects = projects_from_keyword_rows(kw)
    assert projects["SaasWorx"]["competitors"] == ["inoday.com", "linkederp.com"]

    q = [
        ["SaasWorx", "Who are the best NetSuite partners in India?", "NetSuite", "BOFU", "partners, india", "", ""],
        ["SaasWorx", "What is NetSuite?", "", "awareness", "", "TRUE", "claude"],
        ["SaasWorx", "paused question", "", "", "", "0"],
        ["Unknown", "orphan question", "", "", "", ""],
        ["SaasWorx", ""],
    ]
    checks, skipped = parse_question_rows(q, KNOWN, projects)
    assert len(checks) == 2
    a, b = checks
    assert a["kind"] == "question" and a["domain"] == "saasworx.ai" and a["brand"] == "SaasWorx"
    assert a["competitors"] == ["inoday.com", "linkederp.com"]
    assert a["topic"] == "NetSuite" and a["funnel_stage"] == "BOFU" and a["tags"] == ["partners", "india"]
    assert b["funnel_stage"] == "TOFU" and b["platforms"] == ["claude"]
    assert [s["text"] for s in skipped] == ["paused question", "orphan question"]
    assert "no row in the Keywords tab" in skipped[1]["reason"]


def test_results_row_keeps_legacy_columns_in_place():
    row = results_row("2026-10-16", "21:00", "P", "kw", "local", "own", "a.com", 2, 98, True,
                      "b.com", True, "", 1, run_id="r", answer_id="a", ai_app="claude",
                      model="m", prompt_type="keyword", repeat=1, mentioned=True, mention_position=1)
    assert len(row) == len(RESULTS_HEADER)
    assert RESULTS_HEADER[:14] == [
        "Date", "Time (IST)", "Project", "Keyword", "Intent", "Entity Type", "Entity Domain",
        "Rank", "Score", "Cited In Answer", "#1 Result", "Grounded", "Error", "Answer Position"]
    assert row[:14] == ["2026-10-16", "21:00", "P", "kw", "local", "own", "a.com", 2, 98, "yes",
                        "b.com", "yes", "", 1]
    blank = results_row("d", "t", "P", "kw", "local", "own", "a.com", None, None, False, "", False, "boom")
    assert blank[7:14] == ["", "", "no", "", "no", "boom", ""]
