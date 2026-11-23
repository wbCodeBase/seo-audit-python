"""Real-user (observed) engines, per-answer cost, client budgets, manager role.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13

- answers.source ('api' | 'observed') and answers.cost_usd.
- clients.engines (which engines this client is tracked on; [] = all) and
  clients.monthly_budget_usd (NULL = no cap).
- New client member role 'manager' = editor + may run audits for that client
  and see its spend. can_edit_client() now accepts it; can_manage_client()
  is new.
- Cost is agency-internal: on Supabase, answers.cost_usd is readable only
  through geo_spend(), which returns totals for clients the caller manages.
  Column privileges on answers grant every column except cost_usd — a future
  migration that adds a column to answers must grant it too (see
  _grant_answer_columns).
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

FUNCTIONS = """
create or replace function public.can_edit_client(cid integer) returns boolean
language sql stable security definer set search_path = public as $$
  select public.is_admin() or exists (
    select 1 from public.client_members
    where user_id = auth.uid() and client_id = cid and role in ('editor', 'manager'))
$$;

create or replace function public.can_manage_client(cid integer) returns boolean
language sql stable security definer set search_path = public as $$
  select public.is_admin() or exists (
    select 1 from public.client_members
    where user_id = auth.uid() and client_id = cid and role = 'manager')
$$;

-- Spend per client x engine x source between two IST days, only for clients
-- the caller may manage. SECURITY DEFINER because cost_usd isn't readable
-- directly; the can_manage_client() filter is what protects it.
create or replace function public.geo_spend(p_from date, p_to date, p_client integer default null)
returns table (client_id integer, ai_app text, source text, answers bigint, cost_usd double precision)
language sql stable security definer set search_path = public as $$
  select a.client_id, a.ai_app::text, a.source::text, count(*)::bigint,
         coalesce(sum(a.cost_usd), 0)::float8
  from public.answers a
  where (a.created_at at time zone 'Asia/Kolkata')::date between p_from and p_to
    and (p_client is null or a.client_id = p_client)
    and public.can_manage_client(a.client_id)
  group by 1, 2, 3
  order by 1, 2
$$;
"""

DOWN_FUNCTIONS = """
drop function if exists public.geo_spend(date, date, integer);
drop function if exists public.can_manage_client(integer);
create or replace function public.can_edit_client(cid integer) returns boolean
language sql stable security definer set search_path = public as $$
  select public.is_admin() or exists (
    select 1 from public.client_members
    where user_id = auth.uid() and client_id = cid and role = 'editor')
$$;
"""


def _has_supabase_auth(bind):
    if bind.dialect.name != "postgresql":
        return False
    return bind.execute(sa.text(
        "select exists (select 1 from information_schema.tables "
        "where table_schema = 'auth' and table_name = 'users')")).scalar()


def _grant_answer_columns(bind):
    cols = [r[0] for r in bind.execute(sa.text(
        "select column_name from information_schema.columns "
        "where table_schema = 'public' and table_name = 'answers' and column_name <> 'cost_usd'"))]
    op.execute("revoke select on public.answers from anon, authenticated")
    op.execute(f"grant select ({', '.join(cols)}) on public.answers to authenticated")


def upgrade():
    bind = op.get_bind()
    op.add_column("answers", sa.Column("source", sa.String(20), nullable=False, server_default="api"))
    op.add_column("answers", sa.Column("cost_usd", sa.Float))
    op.create_index("ix_answers_client_created", "answers", ["client_id", "created_at"])
    op.add_column("clients", sa.Column("engines", sa.JSON, nullable=False, server_default="[]"))
    op.add_column("clients", sa.Column("monthly_budget_usd", sa.Float))

    if bind.dialect.name == "postgresql":
        op.execute(FUNCTIONS)
    if _has_supabase_auth(bind):
        _grant_answer_columns(bind)


def downgrade():
    bind = op.get_bind()
    if _has_supabase_auth(bind):
        op.execute("grant select on public.answers to authenticated")
    if bind.dialect.name == "postgresql":
        op.execute(DOWN_FUNCTIONS)
    op.drop_column("clients", "monthly_budget_usd")
    op.drop_column("clients", "engines")
    op.drop_index("ix_answers_client_created", table_name="answers")
    op.drop_column("answers", "cost_usd")
    op.drop_column("answers", "source")
