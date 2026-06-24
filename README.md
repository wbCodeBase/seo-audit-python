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


