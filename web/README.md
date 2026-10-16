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
│   ├── login/, auth/         password + email-link sign-in, callback, sign-out
│   ├── (app)/clients/        client list (+ add client, admins)
│   ├── (app)/c/[clientId]/   Overview · Questions · Competitors · Answers
│   └── (app)/admin/users/    invite users, roles, client access (admins)
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
`lib/auth.ts` are for a friendly UX, not the security boundary.

## Run locally

```bash
cd web
cp .env.example .env.local     # NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
npm install
npm run dev                    # http://localhost:3000
```

Checks: `npx tsc --noEmit`, `npm run lint`, `npm run build`.

## Supabase Auth settings (do once)

Supabase dashboard → **Authentication**:

1. **URL Configuration**
   - *Site URL*: your deployed app URL, e.g. `https://app.yourdomain.com`
   - *Redirect URLs*: add `https://app.yourdomain.com/auth/callback`,
     `https://*-yourteam.vercel.app/auth/callback` (previews) and
     `http://localhost:3000/auth/callback`.
2. **Email Templates → Magic Link** — so links also work when opened on a
   different device/browser than the one that requested them, use:
   ```html
   <h2>Sign in to AI Visibility</h2>
   <p><a href="{{ .SiteURL }}/auth/callback?token_hash={{ .TokenHash }}&type=email">Sign in</a></p>
   ```
3. **SMTP Settings** — Supabase's built-in email sender is heavily rate-limited
   and meant for testing. Before inviting clients, add your own SMTP (e.g.
   Resend, Postmark, SES, or your mail provider).
4. **Providers → Email**: keep enabled. Anyone can request a sign-in link, but
   a new account has **no access** until an admin grants it (or it was invited).

## Giving people access

- In the app: **Users → Give someone access** (admins). Works before or after
  the person has signed in; an invitation is applied on their first sign-in.
- From the command line (repo root): `python -m geo invite EMAIL admin` or
  `python -m geo invite EMAIL "Client name" editor`.

## Deploy (Vercel)

Create a **second Vercel project** from the same Git repo:

- *Root Directory*: `web` (framework auto-detected as Next.js)
- *Environment variables*: `NEXT_PUBLIC_SUPABASE_URL`,
  `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
- Add your domain, then put it in the Supabase *Site URL* / *Redirect URLs* above.

The existing Vercel project (repo root) keeps running the Python cron jobs.
