# AI Visibility — web app

The Next.js platform for the GEO audit: sign-in, the client dashboard, buyer
questions, competitor review, the AI answer explorer, and user management.
The data comes from the Python engine in the repo root (`geo/`), via the
shared Supabase Postgres database.

```
src/
├── proxy.ts                  refreshes the Supabase session; sends signed-out users to /login
├── lib/
│   ├── supabase/server.ts    per-request Supabase client acting AS the signed-in user
│   ├── auth.ts               getViewer() / requireClient() / requireAdmin()
│   ├── filters.ts            date range + AI app + topic filters (kept in the URL)
│   └── types.ts              row shapes for tables and dashboard RPCs
├── app/
│   ├── login/, auth/signout/ password sign-in, sign-out
│   ├── (app)/account/        change your own password
│   ├── (app)/clients/        client list (+ add client, admins)
│   ├── (app)/c/[clientId]/   Overview · Questions · Competitors · Answers
│   └── (app)/admin/users/    create accounts, passwords, roles, client access (admins)
└── components/dashboard/     stat tiles, trend chart, bars, questions × AI apps matrix
```

## Security model

The app never uses a privileged key. Every query runs with the signed-in
user's session, and **Postgres Row Level Security** (migrations `0002`/`0003`
in the repo root) decides what comes back: admins see everything, client users
only the clients they're members of; viewers can't change anything, editors
can manage their client's questions and competitor review. The dashboard
numbers come from SQL functions (`geo_metrics`, `geo_competitor_sov`,
`geo_matrix`) that run with the user's permissions too. Page-level checks in
`lib/auth.ts` are for a friendly UX, not the security boundary. The one
exception is `lib/supabase/admin.ts` (secret key, server-only), used solely
for Supabase Auth admin calls (create account, set password, delete) after
the action has checked the caller is an admin, or is changing their own
password after re-entering the current one.

## Run locally

```bash
cd web
cp .env.example .env.local     # Supabase URL, publishable key, SUPABASE_SECRET_KEY
npm install
npm run dev                    # http://localhost:3000
```

Checks: `npx tsc --noEmit`, `npm run lint`, `npm run build`.

## Sign-in (password only — no Supabase email settings needed)

There is no sign-up and no email sending. **Admins create accounts** on the
**Users** page (email + password, with a *Suggest* button for a strong one) and
share the sign-in details themselves. Users change their own password under
**Account** (click your email in the header); an admin can set a new password
for anyone who forgets theirs, or delete an account.

This uses Supabase's official admin API with the project's **secret key**,
on the server only:

- `SUPABASE_SECRET_KEY` — Supabase → Project Settings → API Keys → *Secret
  key*. Set it in Vercel (and in `web/.env.local` for local runs). Never prefix
  it with `NEXT_PUBLIC_`.

**First admin:** Supabase → Authentication → Users → **Add user → Create new
user**, enter the email and a password, tick **Auto Confirm User**. If that
email has an admin invitation (`python -m geo invite EMAIL admin`), it becomes
admin automatically; otherwise run `python -m geo make-admin EMAIL`.

## Giving people access

- In the app: **Users → Add a user** (admins): email, password, and which
  client (viewer/editor) or admin. Leave the password empty for an existing
  account to just add access.
- From the command line (repo root): `python -m geo invite EMAIL admin` or
  `python -m geo invite EMAIL "Client name" editor` — applied when an account
  with that email is created.

## Deploy (Vercel)

Create a **second Vercel project** from the same Git repo:

- *Root Directory*: `web` (`web/vercel.json` pins the framework to Next.js)
- *Environment variables*: `NEXT_PUBLIC_SUPABASE_URL`,
  `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`

The existing Vercel project (repo root) keeps running the Python cron jobs.
