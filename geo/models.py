"""
Postgres schema (SQLAlchemy 2.0). Migrations live in alembic/versions —
change a model here, then add a migration; never edit an applied one.

One "answer" = one AI app's reply to one question in one repeat of one run.
Everything the dashboard shows is derived from answers + mentions + citations,
rolled up nightly into daily_metrics / daily_competitor_metrics.

Access control lives in the database (Postgres RLS, migration 0002): admins
(profiles.role='admin') see everything; client users see only the clients
they're a member of (client_members). Every client-owned table therefore
carries client_id directly, so each policy is a single cheap check.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, Uuid, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# Allowed string values — kept as plain strings (not DB enums) so adding one
# later is a code change, not a migration.
QUESTION_KINDS = ("keyword", "question")
FUNNEL_STAGES = ("TOFU", "MOFU", "BOFU")
RELATIONSHIPS = ("self", "direct", "indirect", "ignored", "pending")
ENTITY_TYPES = ("own", "own brand", "competitor")
USER_ROLES = ("admin", "client")
MEMBER_ROLES = ("viewer", "editor")
QUESTION_SOURCES = ("sheet", "platform")


class Client(Base):
    __tablename__ = "clients"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    name:       Mapped[str] = mapped_column(String(200), unique=True)   # the sheet's "Project"
    domain:     Mapped[str] = mapped_column(String(255))
    brand:      Mapped[str | None] = mapped_column(String(200))
    aliases:    Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    questions:   Mapped[list["Question"]] = relationship(back_populates="client")
    competitors: Mapped[list["Competitor"]] = relationship(back_populates="client")


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("client_id", "kind", "text", name="uq_question"),)

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id:    Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    kind:         Mapped[str] = mapped_column(String(20), default="keyword")
    text:         Mapped[str] = mapped_column(Text)
    topic:        Mapped[str | None] = mapped_column(String(200))
    funnel_stage: Mapped[str | None] = mapped_column(String(10))
    tags:         Mapped[list] = mapped_column(JSON, default=list)
    active:       Mapped[bool] = mapped_column(Boolean, default=True)
    ai_apps:      Mapped[list] = mapped_column(JSON, default=list)   # [] = every configured app
    # Phase 4 scheduling fields; stored now so the sheet sync has a home for them.
    frequency:    Mapped[str] = mapped_column(String(20), default="daily")
    country:      Mapped[str | None] = mapped_column(String(10))
    # sheet = synced from the Google Sheet (paused when its row disappears);
    # platform = created in the web app (the sheet sync never touches it).
    source:       Mapped[str] = mapped_column(String(20), default="sheet")
    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    client: Mapped[Client] = relationship(back_populates="questions")


class Competitor(Base):
    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("client_id", "key", name="uq_competitor"),)

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id:     Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    key:           Mapped[str] = mapped_column(String(255))   # domain, or "name:<compact>" if no domain
    name:          Mapped[str] = mapped_column(String(200))
    domain:        Mapped[str | None] = mapped_column(String(255))
    relationship_: Mapped[str] = mapped_column("relationship", String(20), default="pending")
    source:        Mapped[str] = mapped_column(String(20), default="discovered")   # sheet | discovered
    times_seen:    Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    example_question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"))
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    client: Mapped[Client] = relationship(back_populates="competitors")


class Run(Base):
    __tablename__ = "runs"

    id:          Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trigger:     Mapped[str] = mapped_column(String(20), default="cron")   # cron | manual | import
    status:      Mapped[str] = mapped_column(String(20), default="running")
    started_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats:       Mapped[dict] = mapped_column(JSON, default=dict)


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (Index("ix_answers_question_created", "question_id", "created_at"),)

    id:            Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id:        Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    question_id:   Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    client_id:     Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    ai_app:        Mapped[str] = mapped_column(String(30))
    model:         Mapped[str | None] = mapped_column(String(100))
    repeat:        Mapped[int] = mapped_column(Integer, default=1)
    question_text: Mapped[str] = mapped_column(Text)        # snapshot of what was actually asked
    intent:        Mapped[str | None] = mapped_column(String(30))
    answer_text:   Mapped[str | None] = mapped_column(Text)
    grounded:      Mapped[bool] = mapped_column(Boolean, default=False)
    error:         Mapped[str | None] = mapped_column(Text)
    top_result:    Mapped[str | None] = mapped_column(String(255))
    usage:         Mapped[dict] = mapped_column(JSON, default=dict)
    # 0-100: how positively the answer portrays the client. NULL = not
    # scored yet, or the client isn't named in this answer.
    sentiment:     Mapped[int | None] = mapped_column(Integer)
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    mentions:  Mapped[list["Mention"]] = relationship(cascade="all, delete-orphan")
    citations: Mapped[list["Citation"]] = relationship(cascade="all, delete-orphan")


class Mention(Base):
    """One row per tracked entity per answer — same grain as a Results-tab row."""
    __tablename__ = "mentions"

    id:               Mapped[int] = mapped_column(Integer, primary_key=True)
    answer_id:        Mapped[str] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"), index=True)
    client_id:        Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    competitor_id:    Mapped[int | None] = mapped_column(ForeignKey("competitors.id", ondelete="SET NULL"), index=True)
    entity_type:      Mapped[str] = mapped_column(String(20))    # own | own brand | competitor
    entity_name:      Mapped[str] = mapped_column(String(255))
    mentioned:        Mapped[bool | None] = mapped_column(Boolean)   # named in answer text; NULL = unknown (imported history)
    mention_position: Mapped[int | None] = mapped_column(Integer)
    rank:             Mapped[int | None] = mapped_column(Integer)            # position in raw search results
    score:            Mapped[int | None] = mapped_column(Integer)            # visibility_score(rank, grounded, cited)
    cited:            Mapped[bool] = mapped_column(Boolean, default=False)
    answer_position:  Mapped[int | None] = mapped_column(Integer)            # order first cited in answer


class Citation(Base):
    __tablename__ = "citations"

    id:        Mapped[int] = mapped_column(Integer, primary_key=True)
    answer_id: Mapped[str] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    position:  Mapped[int] = mapped_column(Integer)
    url:       Mapped[str] = mapped_column(Text)
    domain:    Mapped[str] = mapped_column(String(255), index=True)
    title:     Mapped[str | None] = mapped_column(Text)


# ── Users & access ──────────────────────────────────────────────────────────

class Profile(Base):
    """One row per Supabase Auth user (id = auth.users.id), created by a
    trigger on sign-up. Only an admin can change role."""
    __tablename__ = "profiles"

    id:         Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    email:      Mapped[str | None] = mapped_column(String(320))
    role:       Mapped[str] = mapped_column(String(20), default="client")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ClientMember(Base):
    """Which clients a (non-admin) user can see; editors can also manage
    that client's questions and competitor review."""
    __tablename__ = "client_members"

    user_id:    Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    client_id:  Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True, index=True)
    role:       Mapped[str] = mapped_column(String(20), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Invitation(Base):
    """Access granted to an email before that person has signed up. The
    sign-up trigger (migration 0003) turns it into profiles.role='admin' or
    a client_members row the moment they first sign in."""
    __tablename__ = "invitations"

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    email:       Mapped[str] = mapped_column(String(320))          # stored lower-case
    role:        Mapped[str] = mapped_column(String(20), default="client")   # admin | client
    client_id:   Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    member_role: Mapped[str | None] = mapped_column(String(20))   # viewer | editor (client invites)
    invited_by:  Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"))
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("uq_invitations_email_client", Invitation.email, func.coalesce(Invitation.client_id, 0), unique=True)


# ── Nightly roll-ups ────────────────────────────────────────────────────────

class DailyMetric(Base):
    """Per client x question x AI app x day (IST). Raw counts and sums are
    stored alongside the ratios so the dashboard can aggregate any slice
    correctly (ratio of sums, never average of ratios). All rates and scores
    are 0-100."""
    __tablename__ = "daily_metrics"
    __table_args__ = (
        UniqueConstraint("day", "client_id", "question_id", "ai_app", name="uq_daily_metrics"),
        Index("ix_daily_metrics_client_day", "client_id", "day"),
    )

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    day:         Mapped[date] = mapped_column(Date)
    client_id:   Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    ai_app:      Mapped[str] = mapped_column(String(30))

    answers:                     Mapped[int] = mapped_column(Integer, default=0)   # successful answers
    answers_naming_us:           Mapped[int] = mapped_column(Integer, default=0)
    answers_naming_us_or_direct: Mapped[int] = mapped_column(Integer, default=0)
    direct_competitor_mentions:  Mapped[int] = mapped_column(Integer, default=0)
    answers_citing_us:           Mapped[int] = mapped_column(Integer, default=0)
    position_sum:                Mapped[float] = mapped_column(Float, default=0)
    position_n:                  Mapped[int] = mapped_column(Integer, default=0)
    sentiment_sum:               Mapped[float] = mapped_column(Float, default=0)
    sentiment_n:                 Mapped[int] = mapped_column(Integer, default=0)
    sentiment_pending:           Mapped[int] = mapped_column(Integer, default=0)   # naming us, not yet scored
    sentiment_unknowable:        Mapped[int] = mapped_column(Integer, default=0)   # naming us, no text to score (imported)

    mention_rate:     Mapped[float | None] = mapped_column(Float)
    share_of_voice:   Mapped[float | None] = mapped_column(Float)
    position_score:   Mapped[float | None] = mapped_column(Float)
    citation_rate:    Mapped[float | None] = mapped_column(Float)
    covered:          Mapped[bool] = mapped_column(Boolean, default=False)
    sentiment:        Mapped[float | None] = mapped_column(Float)
    visibility_score: Mapped[float | None] = mapped_column(Float)
    estimated:        Mapped[bool] = mapped_column(Boolean, default=False)   # used the imported-history proxy
    computed_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DailyCompetitorMetric(Base):
    """Per competitor, same grain as DailyMetric. A competitor's share of
    voice over any slice = sum(mentions) / (sum(daily_metrics.answers_naming_us)
    + sum(daily_metrics.direct_competitor_mentions))."""
    __tablename__ = "daily_competitor_metrics"
    __table_args__ = (
        UniqueConstraint("day", "client_id", "question_id", "ai_app", "competitor_id",
                         name="uq_daily_competitor_metrics"),
        Index("ix_daily_comp_metrics_client_day", "client_id", "day"),
    )

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    day:           Mapped[date] = mapped_column(Date)
    client_id:     Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    question_id:   Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    ai_app:        Mapped[str] = mapped_column(String(30))
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"))
    answers:       Mapped[int] = mapped_column(Integer, default=0)
    mentions:      Mapped[int] = mapped_column(Integer, default=0)   # answers naming this competitor
    cited:         Mapped[int] = mapped_column(Integer, default=0)
    position_sum:  Mapped[float] = mapped_column(Float, default=0)
    position_n:    Mapped[int] = mapped_column(Integer, default=0)
    computed_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
