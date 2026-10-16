from sqlalchemy import func, select

from geo.importer import group_history, import_answers, run_id_for
from geo.models import Answer, Client, Competitor, Mention, Question, Run

OLD_HEADER = ["Date", "Time (IST)", "Project", "Keyword", "Intent", "Entity Type", "Entity Domain",
              "Rank", "Score", "Cited In Answer", "#1 Result", "Grounded"]
NEW_HEADER = OLD_HEADER + ["Error", "Answer Position", "Run ID"]


def _values():
    return [
        NEW_HEADER,
        ["2026-08-12", "21:00", "SaasWorx", "kw1", "local", "own", "saasworx.ai", "2", "88", "no", "inoday.com", "yes"],
        ["2026-08-12", "21:00", "SaasWorx", "kw1", "local", "own brand", "SaasWorx", "", "0", "no", "inoday.com", "yes"],
        ["2026-08-12", "21:00", "SaasWorx", "kw1", "local", "competitor", "inoday.com", "1", "100", "yes", "inoday.com", "yes"],
        [""] * 12,                                                     # day separator
        ["2026-08-13", "21:00", "SaasWorx", "kw1", "local", "own", "saasworx.ai", "", "", "no", "", "no", "Rate limit", ""],
        ["2026-08-13", "21:00", "SaasWorx", "kw2", "local", "own", "saasworx.ai", "1", "110", "yes", "saasworx.ai", "yes", "", "1"],
        ["2026-10-16", "21:00", "SaasWorx", "kw1", "local", "own", "saasworx.ai", "1", "100", "yes", "x", "yes", "", "1", "run-xyz"],
    ]


def test_group_history_handles_old_and_new_headers():
    answers = group_history(_values(), "claude")
    assert [(a["ts"].date().isoformat(), a["keyword"]) for a in answers] == [
        ("2026-08-12", "kw1"), ("2026-08-13", "kw1"), ("2026-08-13", "kw2")]   # Run ID row skipped
    first = answers[0]
    assert len(first["entities"]) == 3 and first["grounded"] and first["top_result"] == "inoday.com"
    assert first["entities"][0] == {"entity_type": "own", "entity_name": "saasworx.ai", "rank": 2,
                                    "score": 88, "cited": False, "answer_position": None}
    assert answers[1]["error"] == "Rate limit"
    assert answers[2]["entities"][0]["answer_position"] == 1
    assert answers[1]["run_id"] == answers[2]["run_id"] == run_id_for("2026-08-13", "21:00")
    assert group_history([], "claude") == []


def test_two_runs_in_the_same_minute_become_two_answers():
    row = lambda etype, name, rank: ["2026-08-13", "17:43", "P", "kw", "local", etype, name, rank, "", "no", "", "yes"]
    values = [OLD_HEADER, row("own", "a.com", "1"), row("competitor", "b.com", "8"),
              row("own", "a.com", "1"), row("competitor", "b.com", "5")]
    answers = group_history(values, "claude")
    assert [(a["repeat"], [e["rank"] for e in a["entities"]]) for a in answers] == [(1, [1, 8]), (2, [1, 5])]
    assert answers[0]["run_id"] == answers[1]["run_id"]


def test_import_is_idempotent(session):
    answers = group_history(_values(), "claude")
    stats = import_answers(session, answers)
    session.commit()
    assert stats == {"runs": 2, "answers": 3, "skipped_runs": 0}

    client = session.scalar(select(Client))
    assert (client.name, client.domain, client.brand) == ("SaasWorx", "saasworx.ai", "SaasWorx")
    assert session.scalar(select(func.count()).select_from(Question)) == 2
    comp = session.scalar(select(Competitor))
    assert (comp.key, comp.relationship_, comp.source) == ("inoday.com", "direct", "sheet")
    assert session.scalar(select(func.count()).select_from(Mention)) == 4   # error answer has none
    assert all(m.mentioned is None for m in session.scalars(select(Mention)))  # unknown, not False
    assert {r.trigger for r in session.scalars(select(Run))} == {"import"}
    assert session.scalar(select(Answer).where(Answer.error.is_not(None))).mentions == []

    again = import_answers(session, group_history(_values(), "claude"))
    session.commit()
    assert again == {"runs": 0, "answers": 0, "skipped_runs": 2}
    assert session.scalar(select(func.count()).select_from(Answer)) == 3
