"""Invitations + dashboard query functions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16

- invitations: give an email admin or client access before they sign up;
  the sign-up trigger applies it on first sign-in.
- daily_metrics.sentiment_unknowable: needed to aggregate the visibility
  score in SQL exactly like geo/metrics.py does per row.
- Dashboard functions (Postgres only). All are SECURITY INVOKER — they run
  as the signed-in user, so RLS decides what they can see; a client user
  calling them for another client simply gets nothing back.
    geo_metrics(client, from, to, group, apps[], topics[])
        group = 'total' | 'day' | 'app' | 'question' → aggregated KPIs
    geo_competitor_sov(client, from, to, apps[], topics[])
    geo_matrix(client, from, to, apps[], topics[])  → latest answer per question × app
  The visibility formula lives in geo_visibility() and must stay identical
  to geo.metrics.visibility_score().
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TRIGGER_FN = """
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, role, created_at)
  values (new.id, new.email,
          case when exists (select 1 from public.invitations i
                            where i.email = lower(new.email) and i.role = 'admin')
               then 'admin' else 'client' end,
          now())
  on conflict (id) do nothing;

  insert into public.client_members (user_id, client_id, role, created_at)
  select new.id, i.client_id, coalesce(i.member_role, 'viewer'), now()
  from public.invitations i
  where i.email = lower(new.email) and i.client_id is not null
  on conflict do nothing;

  update public.invitations set accepted_at = now()
  where email = lower(new.email) and accepted_at is null;
  return new;
end
$$;
"""

DASHBOARD_FNS = """
create or replace function public.geo_visibility(
  m double precision, p double precision, s double precision, sent double precision,
  pending bigint, unknowable bigint)
returns double precision language sql immutable as $$
  select case
    when coalesce(pending, 0) > 0 then null
    when sent is null and coalesce(unknowable, 0) > 0 then
      round(((0.40 * coalesce(m, 0) + 0.25 * coalesce(p, 0) + 0.20 * coalesce(s, 0)) / 0.85)::numeric, 2)::float8
    else
      round((0.40 * coalesce(m, 0) + 0.25 * coalesce(p, 0) + 0.20 * coalesce(s, 0)
             + 0.15 * coalesce(sent, 0))::numeric, 2)::float8
  end
$$;

create or replace function public.geo_filtered_metrics(
  p_client integer, p_from date, p_to date, p_apps text[] default null, p_topics text[] default null)
returns setof public.daily_metrics language sql stable as $$
  select d.* from public.daily_metrics d
  join public.questions q on q.id = d.question_id
  where d.client_id = p_client and d.day between p_from and p_to
    and (p_apps is null or cardinality(p_apps) = 0 or d.ai_app = any(p_apps))
    and (p_topics is null or cardinality(p_topics) = 0 or q.topic = any(p_topics))
$$;

create or replace function public.geo_metrics(
  p_client integer, p_from date, p_to date, p_group text default 'total',
  p_apps text[] default null, p_topics text[] default null)
returns table (
  grp text, answers bigint, answers_naming_us bigint,
  mention_rate double precision, share_of_voice double precision, position_score double precision,
  citation_rate double precision, coverage double precision, sentiment double precision,
  visibility_score double precision, sentiment_pending bigint, estimated boolean)
language sql stable as $$
  with g as (
    select
      case p_group when 'day' then f.day::text when 'app' then f.ai_app
                   when 'question' then f.question_id::text else 'total' end as grp,
      sum(f.answers)::bigint as n,
      sum(f.answers_naming_us)::bigint as us,
      sum(f.answers_naming_us_or_direct)::bigint as us_or_direct,
      sum(f.direct_competitor_mentions)::bigint as dm,
      sum(f.answers_citing_us)::bigint as cited,
      sum(f.position_sum) as ps, sum(f.position_n)::bigint as pn,
      sum(f.sentiment_sum) as ss, sum(f.sentiment_n)::bigint as sn,
      sum(f.sentiment_pending)::bigint as sp, sum(f.sentiment_unknowable)::bigint as su,
      count(distinct (f.question_id, f.ai_app)) as pairs,
      count(distinct (f.question_id, f.ai_app)) filter (where f.covered) as covered_pairs,
      bool_or(f.estimated) as est
    from public.geo_filtered_metrics(p_client, p_from, p_to, p_apps, p_topics) f
    group by 1
  ), r as (
    select g.*,
      (100.0 * us / nullif(us_or_direct, 0))::float8 as mr,
      (100.0 * us / nullif(us + dm, 0))::float8 as sov,
      (ps / nullif(pn, 0))::float8 as pos,
      (ss / nullif(sn, 0))::float8 as sent
    from g
  )
  select grp, n, us, mr, sov, pos,
         (100.0 * cited / nullif(n, 0))::float8,
         (100.0 * covered_pairs / nullif(pairs, 0))::float8,
         sent, public.geo_visibility(mr, pos, sov, sent, sp, su), sp, est
  from r order by grp
$$;

create or replace function public.geo_competitor_sov(
  p_client integer, p_from date, p_to date, p_apps text[] default null, p_topics text[] default null)
returns table (competitor_id integer, name text, domain text, relationship text,
               mentions bigint, share_of_voice double precision)
language sql stable as $$
  with denom as (
    select sum(answers_naming_us) + sum(direct_competitor_mentions) as d
    from public.geo_filtered_metrics(p_client, p_from, p_to, p_apps, p_topics)
  ), c as (
    select dc.competitor_id, sum(dc.mentions)::bigint as m
    from public.daily_competitor_metrics dc
    join public.questions q on q.id = dc.question_id
    where dc.client_id = p_client and dc.day between p_from and p_to
      and (p_apps is null or cardinality(p_apps) = 0 or dc.ai_app = any(p_apps))
      and (p_topics is null or cardinality(p_topics) = 0 or q.topic = any(p_topics))
    group by 1
  )
  select co.id, co.name::text, co.domain::text, co.relationship::text, c.m,
         (100.0 * c.m / nullif((select d from denom), 0))::float8
  from c join public.competitors co on co.id = c.competitor_id
  order by 6 desc nulls last, co.name
$$;

create or replace function public.geo_matrix(
  p_client integer, p_from date, p_to date, p_apps text[] default null, p_topics text[] default null)
returns table (question_id integer, question text, topic text, ai_app text, answered_at timestamptz,
               answer_id text, named boolean, mention_position integer, rank integer, cited boolean,
               estimated boolean)
language sql stable as $$
  with latest as (
    select distinct on (a.question_id, a.ai_app) a.id, a.question_id, a.ai_app, a.created_at
    from public.answers a
    join public.questions q on q.id = a.question_id
    where a.client_id = p_client and a.error is null
      and (a.created_at at time zone 'Asia/Kolkata')::date between p_from and p_to
      and (p_apps is null or cardinality(p_apps) = 0 or a.ai_app = any(p_apps))
      and (p_topics is null or cardinality(p_topics) = 0 or q.topic = any(p_topics))
    order by a.question_id, a.ai_app, a.created_at desc, a.repeat
  )
  select l.question_id, q.text, q.topic::text, l.ai_app::text, l.created_at, l.id::text,
         coalesce(bool_or(coalesce(m.mentioned, m.cited)), false),
         min(m.mention_position), min(m.rank), coalesce(bool_or(m.cited), false),
         coalesce(bool_or(m.mentioned is null), false)
  from latest l
  join public.questions q on q.id = l.question_id
  left join public.mentions m on m.answer_id = l.id and m.entity_type in ('own', 'own brand')
  group by l.question_id, q.text, q.topic, l.ai_app, l.created_at, l.id
  order by q.text, l.ai_app
$$;
"""

DASHBOARD_FN_SIGNATURES = (
    "geo_matrix(integer, date, date, text[], text[])",
    "geo_competitor_sov(integer, date, date, text[], text[])",
    "geo_metrics(integer, date, date, text, text[], text[])",
    "geo_filtered_metrics(integer, date, date, text[], text[])",
    "geo_visibility(double precision, double precision, double precision, double precision, bigint, bigint)",
)


def _has_supabase_auth(bind):
    if bind.dialect.name != "postgresql":
        return False
    return bind.execute(sa.text(
        "select exists (select 1 from information_schema.tables "
        "where table_schema = 'auth' and table_name = 'users')")).scalar()


def upgrade():
    bind = op.get_bind()
    op.add_column("daily_metrics", sa.Column("sentiment_unknowable", sa.Integer, nullable=False, server_default="0"))

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id", ondelete="CASCADE")),
        sa.Column("member_role", sa.String(20)),
        sa.Column("invited_by", sa.Uuid, sa.ForeignKey("profiles.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("uq_invitations_email_client", "invitations",
                    ["email", sa.text("coalesce(client_id, 0)")], unique=True)

    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "invitations" ENABLE ROW LEVEL SECURITY')
        op.execute(DASHBOARD_FNS)

    if _has_supabase_auth(bind):
        op.execute("create policy invitations_admin on public.invitations for all to authenticated "
                   "using (public.is_admin()) with check (public.is_admin())")
        op.execute(TRIGGER_FN)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for sig in DASHBOARD_FN_SIGNATURES:
            op.execute(f"drop function if exists public.{sig}")
    if _has_supabase_auth(bind):
        op.execute("drop policy if exists invitations_admin on public.invitations")
        # restore the 0002 trigger function (no invitations)
        op.execute("""
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, role, created_at)
  values (new.id, new.email, 'client', now())
  on conflict (id) do nothing;
  return new;
end
$$;
""")
    op.drop_index("uq_invitations_email_client", table_name="invitations")
    op.drop_table("invitations")
    op.drop_column("daily_metrics", "sentiment_unknowable")
