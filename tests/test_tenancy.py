"""Access bootstrap commands, platform-created questions, and sentiment
selection. (RLS policies themselves are Postgres-only; they're exercised
against Supabase, not SQLite.)"""
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from geo import admin, audit, sentiment
from geo.config import get_settings
from geo.models import Answer, Client, ClientMember, Competitor, Invitation, Mention, Profile, Question, Run

from tests.test_audit_db import NOW, _check


def _user(session, email, role="client"):
    p = Profile(id=uuid.uuid4(), email=email, role=role)
    session.add(p)
    session.flush()
    return p


def test_admin_commands(session):
    session.add(Client(name="SaasWorx", domain="saasworx.ai", aliases=[]))
    _user(session, "Owner@Agency.com")
    _user(session, "client@saasworx.ai")
    session.flush()

    assert admin.set_role(session, "owner@agency.com", "admin").role == "admin"   # case-insensitive
    m = admin.add_member(session, "client@saasworx.ai", "SaasWorx")
    assert m.role == "viewer"
    session.flush()
    assert admin.add_member(session, "client@saasworx.ai", "SaasWorx", "editor").role == "editor"
    session.flush()
    assert session.scalar(select(ClientMember)).role == "editor"
    assert admin.remove_member(session, "client@saasworx.ai", "SaasWorx") is True
    assert admin.remove_member(session, "client@saasworx.ai", "SaasWorx") is False

    with pytest.raises(admin.AdminError, match="sign up"):
        admin.set_role(session, "nobody@x.com", "admin")
    with pytest.raises(admin.AdminError, match="Known clients: SaasWorx"):
        admin.add_member(session, "client@saasworx.ai", "Nope")
    with pytest.raises(admin.AdminError):
        admin.add_member(session, "client@saasworx.ai", "SaasWorx", "owner")


def test_platform_questions_run_and_survive_sheet_sync(session):
    ctxs = audit.sync_config(session, [_check()], NOW)
    cid = ctxs["SaasWorx"]["client_id"]
    session.add_all([
        Question(client_id=cid, kind="question", text="Who is the best NetSuite partner?", topic="NetSuite",
                 funnel_stage="BOFU", tags=["partners"], ai_apps=["chatgpt"], active=True,
                 frequency="daily", source="platform"),
        Question(client_id=cid, kind="question", text="paused one", tags=[], ai_apps=[], active=False,
                 frequency="daily", source="platform"),
        Competitor(client_id=cid, key="linkederp.com", name="LinkedERP", domain="linkederp.com",
                   relationship_="direct", source="discovered", times_seen=1),
    ])
    session.flush()

    # A sheet sync where the platform question isn't in the sheet must not pause it.
    ctxs = audit.sync_config(session, [_check()], NOW)
    providers = {"claude": {}, "chatgpt": {}}
    checks = audit.load_platform_checks(session, providers, ctxs)
    assert [c["text"] for c in checks] == ["Who is the best NetSuite partner?"]
    ch = checks[0]
    assert ch["platforms"] == ["chatgpt"] and ch["funnel_stage"] == "BOFU" and ch["domain"] == "saasworx.ai"
    assert ch["competitors"] == ["inoday.com", "linkederp.com"]
    assert ("question", ch["text"]) in ctxs["SaasWorx"]["question_ids"]


def test_platform_only_client_gets_a_context(session):
    c = Client(name="Acme", domain="acme.com", brand="Acme", aliases=["ACME Corp"])
    session.add(c)
    session.flush()
    session.add(Question(client_id=c.id, kind="question", text="best widgets?", tags=[], ai_apps=[],
                         active=True, frequency="daily", source="platform"))
    session.flush()
    ctxs = {}
    checks = audit.load_platform_checks(session, {"claude": {}}, ctxs)
    assert checks[0]["platforms"] == ["claude"] and ctxs["Acme"]["self_aliases"] == ["ACME Corp"]


def test_sentiment_selects_only_scoreable_named_answers(session, monkeypatch):
    c = Client(name="S", domain="s.com", brand="S", aliases=[])
    session.add(c)
    session.flush()
    q = Question(client_id=c.id, kind="keyword", text="kw", tags=[], ai_apps=[], active=True, frequency="daily")
    session.add_all([q, Run(id="r", trigger="cron", started_at=NOW)])
    session.flush()

    def add(aid, named, text="text", error=None, score=None):
        session.add(Answer(id=aid, run_id="r", question_id=q.id, client_id=c.id, ai_app="claude", repeat=1,
                           question_text="kw", grounded=True, usage={}, created_at=NOW, answer_text=text,
                           error=error, sentiment=score))
        session.flush()
        session.add(Mention(answer_id=aid, client_id=c.id, entity_type="own brand", entity_name="S",
                            mentioned=named, cited=False))
    add("yes", True)
    add("not-named", False)
    add("no-text", True, text=None)
    add("errored", True, error="boom")
    add("done", True, score=50)
    add("imported", None)
    session.flush()
    since = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert [a.id for a, _ in sentiment.answers_needing_sentiment(session, since, 100)] == ["yes"]

    settings = replace(get_settings(), anthropic_api_key="k", sentiment_enabled=True)
    monkeypatch.setattr(sentiment, "score_one", lambda *a: (82, None))
    assert sentiment.score_pending(session, settings, since) == {"scored": 1, "failed": 0}
    assert session.get(Answer, "yes").sentiment == 82

    no_key = replace(settings, anthropic_api_key="")
    assert sentiment.score_pending(session, no_key, since)["scored"] == 0


def test_invite_before_and_after_signup(session):
    session.add(Client(name="SaasWorx", domain="saasworx.ai", aliases=[]))
    session.flush()
    assert admin.invite(session, " DevOps@Example.com ", "admin") == "invited"
    assert admin.invite(session, "devops@example.com", "admin") == "invited"          # no duplicate
    assert admin.invite(session, "c@x.com", "client", "SaasWorx", "editor") == "invited"
    session.flush()
    invs = session.scalars(select(Invitation).order_by(Invitation.id)).all()
    assert [(i.email, i.role, i.member_role) for i in invs] == [
        ("devops@example.com", "admin", None), ("c@x.com", "client", "editor")]

    _user(session, "existing@x.com")
    assert admin.invite(session, "existing@x.com", "client", "SaasWorx", "viewer") == "applied"
    session.flush()
    assert session.scalar(select(ClientMember)).role == "viewer"
    with pytest.raises(admin.AdminError):
        admin.invite(session, "z@x.com", "client", "Nope")
