# SEO Audit Tool + AI Visibility (GEO) Audit

This repo hosts three things:

1. **AI Visibility (GEO) audit** — a daily job that asks AI apps (Claude,
   ChatGPT) your clients' keywords and buyer questions with live web search,
   and records whether each client and its competitors are found, cited and
   *named* in the answer. Results go to **Postgres (Supabase)** and, while
   we transition, to the Google Sheet too. Day-to-day usage for the team is in
   [AUDIT_USER_MANUAL.md](AUDIT_USER_MANUAL.md).
2. **The platform** — a Next.js web app in [`web/`](web/README.md) where the
   team and clients sign in to see dashboards, manage questions and review
   competitors. Deployed as its own Vercel project (Root Directory `web`).
3. The original **SEO site audit / AI rank checker** UI (`public/`, `api/audit_*`,
   `api/ai_rank_*`) — unchanged; its deploy guide is further down.

## GEO audit — how it fits together

```
Google Sheet (Keywords + Questions tabs)        ← settings input
        │
        ▼
api/scheduled_audit.py  (Vercel Cron, 21:00 IST)  →  geo/audit.py
        │
        ├─ geo/providers.py   ask Claude / ChatGPT (web search on)
        ├─ geo/parsing.py     rankings, full answer text, cited URLs
        ├─ geo/matching.py    rank / cited / named-in-answer per entity
        ├─ geo/scoring.py     rank → score rule (unchanged)
        ├─ geo/discovery.py   flag brands we don't track yet (pending review)
        │
        ├──▶ Postgres (geo/models.py): clients, questions, competitors,
        │                               runs, answers, mentions, citations
        └──▶ "Results - <App>" tabs   (while SHEETS_DUAL_WRITE=true)

api/compute_metrics.py  (Vercel Cron, 22:00 IST)  →  geo/metrics.py
        ├─ geo/sentiment.py   score answers that name the client (0–100)
        └──▶ daily_metrics, daily_competitor_metrics   ← what the dashboard reads

Next.js app (web/, Phase 3) ── Supabase Auth + RLS ──▶ same database
```

Questions come from the sheet **and** from the web app (`questions.source =
'platform'`); the sheet sync only ever pauses sheet-sourced questions.

| Path | What |
|---|---|
| `geo/` | All audit logic, shared by the cron, CLI, import script and (later) workers/API |
| `alembic/` | Database migrations (`alembic upgrade head`) |
| `scripts/import_sheet_history.py` | One-off import of existing Results-tab history |
| `tests/` | pytest suite — no network or API keys needed |
| `docker-compose.yml` | Local app + Postgres + Redis |
| `geo/metrics.py` | Nightly roll-up — formulas documented at the top of the file |
| `geo/admin.py` | Grant admin / client access from the command line |
| `web/` | Next.js platform — setup, Supabase Auth settings and deploy steps in [web/README.md](web/README.md) |

## GEO audit — setup

### 1. Local environment

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
cp .env.example .env              # then fill in the values — .env is gitignored
pytest -q                         # should be all green
```

### 2. Database (Supabase)

1. Supabase dashboard → your project → **Connect**.
2. Copy the **Transaction pooler** string (port **6543**) into `DATABASE_URL`, and the
   **Session pooler** string (port **5432**) into `DATABASE_URL_DIRECT`. Replace
   `[YOUR-PASSWORD]` with your database password (Project Settings → Database →
   reset it there if you don't have it). Keep these in `.env` / Vercel only.
3. Create the tables:
   ```bash
   alembic upgrade head
   ```
   This also enables Row Level Security on every table, so the public
   (publishable/anon) key can't read them through Supabase's REST API.
4. Import the existing sheet history (safe to re-run):
   ```bash
   python scripts/import_sheet_history.py --dry-run   # counts only
   python scripts/import_sheet_history.py
   ```

### 3. Vercel environment variables

Project → Settings → Environment Variables (then **Redeploy**):

| Name | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | at least one | An app with no key is skipped |
| `GOOGLE_SHEET_ID` | yes | From the sheet URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | yes | Raw or base64 JSON; share the sheet with the service account |
| `CRON_SECRET` | yes | Vercel Cron sends it; use `?secret=` for manual runs |
| `DATABASE_URL` | for DB | Supabase **transaction pooler** URL (6543) |
| `SHEETS_DUAL_WRITE` | no (default `true`) | Set `false` to stop writing Results tabs |
| `REPEATS_PER_RUN` | no (default `1`) | Ask each question N times per run |
| `DISCOVERY_ENABLED` | no (default `true`) | One cheap Haiku call per answer to spot new competitors |
| `SENTIMENT_ENABLED` | no (default `true`) | One cheap Haiku call per answer naming the client |
| `METRICS_DAYS` | no (default `7`) | Window the nightly metrics job recomputes |

Without `DATABASE_URL` the audit behaves exactly as before (sheet only). If the
database is unreachable during a run, it falls back to the sheet and reports
`db_error` in the response instead of failing.

### 4. Running an audit

- **Scheduled:** Vercel Cron, daily 15:30 UTC (21:00 IST) — see `vercel.json`.
- **On demand (deployed):** `https://<your-app>.vercel.app/api/scheduled_audit?secret=<CRON_SECRET>`
- **Locally:** `python -m geo run` (uses real API credits; prints the JSON result).
- **Sheet settings only:** `python -m geo sync` copies the sheet's clients,
  questions and competitors into the database without asking any AI (no cost).

### 5. Users and access

Who sees what is enforced **in the database** (Postgres Row Level Security,
migration `0002`), so the web app, Supabase's REST API and anything else
holding a user's login get the same rules:

| Who | Sees | Can change |
|---|---|---|
| **Admin** (in-house, `profiles.role = 'admin'`) | Every client, runs, users | Everything |
| **Client editor** (`client_members.role = 'editor'`) | Only their client(s) | That client's questions and competitor review |
| **Client viewer** (`client_members.role = 'viewer'`) | Only their client(s) | Nothing |
| Not signed in (publishable key) | Nothing | Nothing |

Every Supabase sign-up automatically gets a `profiles` row with role `client`
and no access until someone grants it. Until the web app has admin screens:

```bash
python -m geo invite you@agency.com admin                      # works before they've signed up
python -m geo invite person@client.com "SaasWorx" editor       # or viewer
python -m geo make-admin you@agency.com
python -m geo add-member person@client.com "SaasWorx" editor   # or viewer
python -m geo remove-member person@client.com "SaasWorx"
```

### 6. Metrics

`daily_metrics` holds one row per client × question × AI app × day (IST),
with raw counts **and** ratios. When summing over several rows, add up the
counts and divide (never average the ratios): e.g. mention rate =
Σ`answers_naming_us` ÷ Σ`answers_naming_us_or_direct`.

| Metric | Definition |
|---|---|
| Mention rate | answers naming us ÷ answers naming us or a **direct** competitor |
| Share of voice | answers naming us ÷ (answers naming us + direct-competitor mentions) |
| Position score | average of the existing rank → score rule (best of domain/brand) |
| Citation rate | answers citing us ÷ answers |
| Coverage | question × AI app pairs where we were named at least once |
| Sentiment | average 0–100 score of answers that name us |
| Visibility score | 0.40×mention + 0.25×position + 0.20×SoV + 0.15×sentiment |

Visibility is left empty while sentiment is still unscored (no
`ANTHROPIC_API_KEY` yet). Imported sheet history never recorded whether a
brand was *named*, so "cited" stands in for it there and those rows have
`estimated = true`.

```bash
python -m geo metrics               # last METRICS_DAYS days (same as the 22:00 IST cron)
python -m geo metrics --days 365    # backfill
```

### 7. Docker (local Postgres instead of Supabase)

```bash
docker compose up -d db redis
docker compose run --rm app                     # alembic upgrade head
docker compose run --rm app pytest -q
docker compose run --rm app python -m geo run   # real run against local DB
```

The `app` container reads API keys from `.env` but always uses the local `db`
service, never Supabase.

### Changing the schema

Edit `geo/models.py`, then add a migration in `alembic/versions/`
(`alembic revision --autogenerate -m "..."` against a local DB, review it),
commit both, and run `alembic upgrade head` on Supabase before deploying code
that needs it.

---

# SEO Audit Tool — Vercel Deploy Guide

## Project structure

```
seo_audit_vercel/
├── api/
│   ├── audit_start.py   ← POST /api/audit_start  (fast, <5s)
│   ├── audit_poll.py    ← GET  /api/audit_poll    (fast, <1s)
│   └── audit_run.py     ← POST /api/audit_run     (long, 60-120s)
├── public/
│   └── index.html       ← Full UI
├── requirements.txt
├── vercel.json
└── README.md
```

---

## Step 1 — Push to GitHub

```bash
git init
git add .
git commit -m "SEO Audit Tool"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/seo-audit-tool.git
git push -u origin main
```

---

## Step 2 — Deploy on Vercel

1. Go to **vercel.com** and log in
2. Click **Add New → Project**
3. Click **Import** next to your GitHub repo
4. Leave all settings as default
5. Click **Deploy**

---

## Step 3 — Add environment variables (REQUIRED)

Go to: **Vercel Dashboard → Your Project → Settings → Environment Variables**

Add these two variables:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...` (from console.anthropic.com) |
| `UPSTASH_REDIS_REST_URL` | `https://xxx.upstash.io` (from upstash.com — free) |
| `UPSTASH_REDIS_REST_TOKEN` | your Upstash token |

After adding env vars → go to **Deployments → ⋯ → Redeploy**

### Why Upstash Redis?
Vercel serverless functions are stateless — each request runs in isolation.
Redis lets `audit_run` store the result so `audit_poll` can retrieve it.
Upstash has a **free tier** (10,000 requests/day, no credit card needed).

**Get Upstash Redis free:**
1. Go to **upstash.com** → Sign up
2. Create Database → Choose region → Copy REST URL and REST Token
3. Paste into Vercel env vars above

---

## Step 4 — Set function timeout (Vercel Pro only)

On **Hobby (free)** tier: functions timeout at **60 seconds**.
Simple sites audit in ~45s — usually fine.
Complex sites may timeout.

On **Pro ($20/mo)**: go to Project Settings → Functions → set `maxDuration` to `300`.

---

## Embed on your website

### Basic iframe
```html
<iframe
  src="https://your-project.vercel.app"
  width="100%"
  height="900"
  frameborder="0"
  style="border-radius:12px; box-shadow:0 4px 24px rgba(0,0,0,0.1);"
></iframe>
```

### Responsive full-height
```html
<div style="position:relative; width:100%; padding-bottom:90vh; height:0; overflow:hidden;">
  <iframe
    src="https://your-project.vercel.app"
    style="position:absolute; top:0; left:0; width:100%; height:100%; border:none; border-radius:12px;"
  ></iframe>
</div>
```

### WordPress
Use **Gutenberg → Custom HTML block** and paste either iframe above.
Or install the **"Advanced iFrame"** plugin for more control.

### Webflow
Add → **Embed element** → paste the iframe code.

### Wix
Add → **Embed** → **Embed a Site** → paste your Vercel URL.

---

## Custom domain (optional)

Vercel Dashboard → Your Project → Settings → Domains
→ Add `seoaudit.yourdomain.com`
→ Add a CNAME record in your domain registrar pointing to `cname.vercel-dns.com`

---

## Troubleshooting

**Build error "pattern doesn't match"**
→ Already fixed in this version. The `vercel.json` uses `builds` not `functions`.

**"Audit timed out"**
→ Upgrade to Vercel Pro and set maxDuration to 300s in Project Settings → Functions.

**Report never loads (spins forever)**
→ Check that Upstash Redis env vars are set correctly and redeployed.

**Blank page on embed**
→ Add this to `vercel.json` headers section if your site blocks iframes:
```json
"headers": [{"source": "/(.*)", "headers": [{"key": "X-Frame-Options", "value": "ALLOWALL"}]}]
```


