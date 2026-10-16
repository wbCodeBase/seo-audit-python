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

## Supabase sign-in settings (do once, ~5 minutes)

Replace `YOUR-APP` below with your web app's address on Vercel
(e.g. `seo-audit-web.vercel.app`, or your own domain later).

**1. Tell Supabase where the website is** — otherwise sign-in emails point to
`localhost`.
Supabase → **Authentication** → **URL Configuration**
- *Site URL*: `https://YOUR-APP`
- *Redirect URLs* → **Add URL**, add both:
  - `https://YOUR-APP/auth/callback`
  - `http://localhost:3000/auth/callback`

**2. Make the sign-in email link work on any device** — by default it only
works in the same browser where you typed your email.
Supabase → **Authentication** → **Emails** → **Magic Link** → replace the
message body with:
```html
<h2>Sign in to AI Visibility</h2>
<p><a href="{{ .SiteURL }}/auth/callback?token_hash={{ .TokenHash }}&type=email">Sign in</a></p>
```
→ **Save**.

**3. (Before inviting clients) your own email sender** — Supabase's built-in
sender only sends a few emails per hour; fine for testing yourself. Later:
Supabase → **Authentication** → **Emails** → **SMTP Settings**, and connect a
service such as Resend, Postmark or your company mail.

Then open `https://YOUR-APP`, type your email, click **Email me a sign-in
link**, and click the link in the email. Invited emails get their access
automatically on first sign-in.

## Giving people access

- In the app: **Users → Give someone access** (admins). Works before or after
  the person has signed in; an invitation is applied on their first sign-in.
- From the command line (repo root): `python -m geo invite EMAIL admin` or
  `python -m geo invite EMAIL "Client name" editor`.

## Deploy (Vercel)

Create a **second Vercel project** from the same Git repo:

- *Root Directory*: `web` (`web/vercel.json` pins the framework to Next.js)
- *Environment variables*: `NEXT_PUBLIC_SUPABASE_URL`,
  `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
- Add your domain, then put it in the Supabase *Site URL* / *Redirect URLs* above.

The existing Vercel project (repo root) keeps running the Python cron jobs.
