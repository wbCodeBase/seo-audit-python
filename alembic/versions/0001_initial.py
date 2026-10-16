"""Initial schema: clients, questions, competitors, runs, answers, mentions, citations.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("clients", "questions", "competitors", "runs", "answers", "mentions", "citations")


def _ts(name, **kw):
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def upgrade():
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("brand", sa.String(200)),
        sa.Column("aliases", sa.JSON, nullable=False),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
    )
    op.create_table(
        "questions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("topic", sa.String(200)),
        sa.Column("funnel_stage", sa.String(10)),
        sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("ai_apps", sa.JSON, nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("country", sa.String(10)),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        sa.UniqueConstraint("client_id", "kind", "text", name="uq_question"),
    )
    op.create_table(
        "competitors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(255)),
        sa.Column("relationship", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("times_seen", sa.Integer, nullable=False),
        _ts("first_seen_at"),
        _ts("last_seen_at"),
        sa.Column("example_question_id", sa.Integer, sa.ForeignKey("questions.id", ondelete="SET NULL")),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("client_id", "key", name="uq_competitor"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _ts("started_at", nullable=False, index=True),
        _ts("finished_at"),
        sa.Column("stats", sa.JSON, nullable=False),
    )
    op.create_table(
        "answers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("question_id", sa.Integer, sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ai_app", sa.String(30), nullable=False),
        sa.Column("model", sa.String(100)),
        sa.Column("repeat", sa.Integer, nullable=False),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("intent", sa.String(30)),
        sa.Column("answer_text", sa.Text),
        sa.Column("grounded", sa.Boolean, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("top_result", sa.String(255)),
        sa.Column("usage", sa.JSON, nullable=False),
        _ts("created_at", nullable=False),
    )
    op.create_index("ix_answers_question_created", "answers", ["question_id", "created_at"])
    op.create_table(
        "mentions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("answer_id", sa.String(36), sa.ForeignKey("answers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("competitor_id", sa.Integer, sa.ForeignKey("competitors.id", ondelete="SET NULL"), index=True),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_name", sa.String(255), nullable=False),
        sa.Column("mentioned", sa.Boolean),
        sa.Column("mention_position", sa.Integer),
        sa.Column("rank", sa.Integer),
        sa.Column("score", sa.Integer),
        sa.Column("cited", sa.Boolean, nullable=False),
        sa.Column("answer_position", sa.Integer),
    )
    op.create_table(
        "citations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("answer_id", sa.String(36), sa.ForeignKey("answers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("domain", sa.String(255), nullable=False, index=True),
        sa.Column("title", sa.Text),
    )

    # Supabase exposes every table in the public schema through its REST API
    # to anyone holding the publishable (anon) key. Enabling RLS with no
    # policies blocks that path entirely; the backend connects as the table
    # owner via DATABASE_URL, which RLS doesn't restrict.
    if op.get_bind().dialect.name == "postgresql":
        for t in TABLES:
            op.execute(f'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    for t in reversed(TABLES):
        op.drop_table(t)
