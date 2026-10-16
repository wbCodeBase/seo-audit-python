"""Tenancy (profiles, client_members, RLS policies) and nightly metrics tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16

Access model (enforced by Postgres RLS, so every client of the database —
the Next.js app, Supabase's REST API, anything holding a user's JWT — gets
the same rules):
  - admin  (profiles.role = 'admin')       → sees and manages everything
  - client member, role 'viewer'           → reads only their client(s)
  - client member, role 'editor'           → + manages that client's
                                             questions and competitor review
The Python engine connects as the table owner and is not restricted.

The auth-dependent parts (auth.users FK, sign-up trigger, policies) only run
when the database has Supabase's auth schema — local Docker Postgres and
SQLite (tests) get the plain tables.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NEW_TABLES = ("profiles", "client_members", "daily_metrics", "daily_competitor_metrics")

# table → (select rule, extra policies)
CLIENT_READ_TABLES = ("answers", "mentions", "citations", "daily_metrics", "daily_competitor_metrics")

FUNCTIONS = """
create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role = 'admin')
$$;

create or replace function public.can_view_client(cid integer) returns boolean
language sql stable security definer set search_path = public as $$
  select public.is_admin() or exists (
    select 1 from public.client_members where user_id = auth.uid() and client_id = cid)
$$;

create or replace function public.can_edit_client(cid integer) returns boolean
language sql stable security definer set search_path = public as $$
  select public.is_admin() or exists (
    select 1 from public.client_members
    where user_id = auth.uid() and client_id = cid and role = 'editor')
$$;

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, role, created_at)
  values (new.id, new.email, 'client', now())
  on conflict (id) do nothing;
  return new;
end
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();
"""

POLICIES = """
create policy clients_read  on public.clients for select to authenticated using (public.can_view_client(id));
create policy clients_admin on public.clients for all    to authenticated using (public.is_admin()) with check (public.is_admin());

create policy questions_read   on public.questions for select to authenticated using (public.can_view_client(client_id));
create policy questions_insert on public.questions for insert to authenticated
  with check (public.can_edit_client(client_id) and (public.is_admin() or source = 'platform'));
create policy questions_update on public.questions for update to authenticated
  using (public.can_edit_client(client_id)) with check (public.can_edit_client(client_id));
create policy questions_delete on public.questions for delete to authenticated using (public.can_edit_client(client_id));

create policy competitors_read   on public.competitors for select to authenticated using (public.can_view_client(client_id));
create policy competitors_insert on public.competitors for insert to authenticated with check (public.can_edit_client(client_id));
create policy competitors_update on public.competitors for update to authenticated
  using (public.can_edit_client(client_id)) with check (public.can_edit_client(client_id));
create policy competitors_delete on public.competitors for delete to authenticated using (public.is_admin());

create policy runs_admin on public.runs for select to authenticated using (public.is_admin());

create policy profiles_read  on public.profiles for select to authenticated using (id = auth.uid() or public.is_admin());
create policy profiles_admin on public.profiles for update to authenticated using (public.is_admin()) with check (public.is_admin());

create policy members_read  on public.client_members for select to authenticated using (user_id = auth.uid() or public.is_admin());
create policy members_admin on public.client_members for all    to authenticated using (public.is_admin()) with check (public.is_admin());
""" + "".join(
    f"create policy {t}_read on public.{t} for select to authenticated using (public.can_view_client(client_id));\n"
    for t in CLIENT_READ_TABLES
)

ALL_POLICIES = [
    ("clients", "clients_read"), ("clients", "clients_admin"),
    ("questions", "questions_read"), ("questions", "questions_insert"),
    ("questions", "questions_update"), ("questions", "questions_delete"),
    ("competitors", "competitors_read"), ("competitors", "competitors_insert"),
    ("competitors", "competitors_update"), ("competitors", "competitors_delete"),
    ("runs", "runs_admin"), ("profiles", "profiles_read"), ("profiles", "profiles_admin"),
    ("client_members", "members_read"), ("client_members", "members_admin"),
] + [(t, f"{t}_read") for t in CLIENT_READ_TABLES]


def _has_supabase_auth(bind):
    if bind.dialect.name != "postgresql":
        return False
    return bind.execute(sa.text(
        "select exists (select 1 from information_schema.tables "
        "where table_schema = 'auth' and table_name = 'users')")).scalar()


def _ts(name, **kw):
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def _add_client_id(table):
    """Add client_id, backfill it from the parent answer, then lock it down."""
    op.add_column(table, sa.Column("client_id", sa.Integer, nullable=True))
    op.execute(f"update {table} set client_id = "
               f"(select answers.client_id from answers where answers.id = {table}.answer_id)")
    with op.batch_alter_table(table) as b:
        b.alter_column("client_id", existing_type=sa.Integer, nullable=False)
        b.create_foreign_key(f"{table}_client_id_fkey", "clients", ["client_id"], ["id"], ondelete="CASCADE")
    op.create_index(f"ix_{table}_client_id", table, ["client_id"])


def upgrade():
    bind = op.get_bind()

    op.add_column("questions", sa.Column("source", sa.String(20), nullable=False, server_default="sheet"))
    op.add_column("answers", sa.Column("sentiment", sa.Integer))
    _add_client_id("mentions")
    _add_client_id("citations")

    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("email", sa.String(320)),
        sa.Column("role", sa.String(20), nullable=False),
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "client_members",
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True, index=True),
        sa.Column("role", sa.String(20), nullable=False),
        _ts("created_at", nullable=False),
    )
    op.create_table(
        "daily_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Integer, sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_app", sa.String(30), nullable=False),
        sa.Column("answers", sa.Integer, nullable=False),
        sa.Column("answers_naming_us", sa.Integer, nullable=False),
        sa.Column("answers_naming_us_or_direct", sa.Integer, nullable=False),
        sa.Column("direct_competitor_mentions", sa.Integer, nullable=False),
        sa.Column("answers_citing_us", sa.Integer, nullable=False),
        sa.Column("position_sum", sa.Float, nullable=False),
        sa.Column("position_n", sa.Integer, nullable=False),
        sa.Column("sentiment_sum", sa.Float, nullable=False),
        sa.Column("sentiment_n", sa.Integer, nullable=False),
        sa.Column("sentiment_pending", sa.Integer, nullable=False),
        sa.Column("mention_rate", sa.Float),
        sa.Column("share_of_voice", sa.Float),
        sa.Column("position_score", sa.Float),
        sa.Column("citation_rate", sa.Float),
        sa.Column("covered", sa.Boolean, nullable=False),
        sa.Column("sentiment", sa.Float),
        sa.Column("visibility_score", sa.Float),
        sa.Column("estimated", sa.Boolean, nullable=False),
        _ts("computed_at", nullable=False),
        sa.UniqueConstraint("day", "client_id", "question_id", "ai_app", name="uq_daily_metrics"),
    )
    op.create_index("ix_daily_metrics_client_day", "daily_metrics", ["client_id", "day"])
    op.create_table(
        "daily_competitor_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Integer, sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_app", sa.String(30), nullable=False),
        sa.Column("competitor_id", sa.Integer, sa.ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("answers", sa.Integer, nullable=False),
        sa.Column("mentions", sa.Integer, nullable=False),
        sa.Column("cited", sa.Integer, nullable=False),
        sa.Column("position_sum", sa.Float, nullable=False),
        sa.Column("position_n", sa.Integer, nullable=False),
        _ts("computed_at", nullable=False),
        sa.UniqueConstraint("day", "client_id", "question_id", "ai_app", "competitor_id",
                            name="uq_daily_competitor_metrics"),
    )
    op.create_index("ix_daily_comp_metrics_client_day", "daily_competitor_metrics", ["client_id", "day"])

    if bind.dialect.name == "postgresql":
        for t in NEW_TABLES + ("alembic_version",):
            op.execute(f'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY')

    if _has_supabase_auth(bind):
        op.execute("alter table public.profiles add constraint profiles_id_fkey "
                   "foreign key (id) references auth.users(id) on delete cascade")
        op.execute("insert into public.profiles (id, email, role, created_at) "
                   "select id, email, 'client', now() from auth.users on conflict (id) do nothing")
        op.execute(FUNCTIONS)
        op.execute(POLICIES)


def downgrade():
    bind = op.get_bind()
    if _has_supabase_auth(bind):
        for table, name in ALL_POLICIES:
            op.execute(f"drop policy if exists {name} on public.{table}")
        op.execute("drop trigger if exists on_auth_user_created on auth.users")
        for fn in ("handle_new_user()", "can_edit_client(integer)", "can_view_client(integer)", "is_admin()"):
            op.execute(f"drop function if exists public.{fn}")
    for t in reversed(NEW_TABLES):
        op.drop_table(t)
    for table in ("citations", "mentions"):
        op.drop_index(f"ix_{table}_client_id", table_name=table)
        with op.batch_alter_table(table) as b:
            b.drop_constraint(f"{table}_client_id_fkey", type_="foreignkey")
            b.drop_column("client_id")
    op.drop_column("answers", "sentiment")
    op.drop_column("questions", "source")
