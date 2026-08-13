# AI Ranking Audit — Team Manual

This is the day-to-day guide for running and reading the daily AI visibility
audit. It lives entirely in one Google Sheet — nobody needs to touch code or
Vercel to use it day to day. Engineering-only setup (API keys, service
account) is in the main [README.md](README.md); this doc is for whoever adds
keywords and reads results.

---

## 1. What this actually checks

For every keyword you list, the tool asks Claude and ChatGPT the query with
their **live web search** turned on — the same as if you typed that exact
question into Claude.ai or ChatGPT yourself — and records:

- Where your domain showed up in the real search results (not a guess from
  the AI's memory).
- Whether the AI directly quoted/referenced your site in its written answer.
- The same two things for each competitor domain you listed.

It runs automatically every day at **9:00 PM IST**, and can also be run
on demand (ask engineering for the manual-trigger link if you need results
sooner than the next scheduled run).

**Important mental model**: this is not a Google ranking checker. Claude's
and ChatGPT's web search can genuinely return different results for the
identical query on different days — it isn't a fixed, cached top-10 the way
Google's index is. That's real behavior, not a bug in the tool. Judge a
domain's visibility from the trend across several days, not any single
day's row. See [§5](#5-reading-the-results-tabs) for what each column means
and how to read it correctly.

---

## 2. Adding keywords — the Keywords tab

Everything the tool audits comes from one tab named **Keywords**. Adding a
row there is the entire "add a keyword" workflow — nothing else to do, no
one needs to redeploy or touch code. The next run (scheduled or manual)
picks up new/edited rows automatically.

| Column | Meaning | Required? |
|---|---|---|
| **Project** | A label for grouping, e.g. the client name. Shows up in every result row so you can filter/pivot by it. | Optional — falls back to the Domain if left blank |
| **Domain** | The domain you're tracking, e.g. `saasworx.ai` | **Required** |
| **Brand** | The brand/company name as it'd appear in text, e.g. `SaasWorx` | Optional, but recommended — some mentions cite the brand name without linking the domain |
| **Competitors** | Comma-separated competitor domains to compare against for this exact keyword, e.g. `inoday.com, linkederp.com` | Optional |
| **Keyword** | The exact query to audit, e.g. `netsuite implementation partners in Noida` | **Required** |
| **Active** | `TRUE` to include this row in daily runs, `FALSE` to pause it without deleting it. Blank counts as `TRUE`. | Optional |
| **Platforms** | Which AI platform(s) to check for this row: `claude`, `chatgpt`, or both comma-separated. Blank = every platform currently enabled. | Optional |

**Example row:**

| Project | Domain | Brand | Competitors | Keyword | Active | Platforms |
|---|---|---|---|---|---|---|
| SaasWorx | saasworx.ai | SaasWorx | inoday.com, linkederp.com | netsuite implementation partners in Noida | TRUE | |

### Common tasks

- **Add a new keyword for an existing client** → new row, same Project/Domain/Brand/Competitors, different Keyword.
- **Add a brand-new client** → new row with that client's own Domain/Brand/Competitors.
- **Pause a keyword temporarily** → set Active to `FALSE`. Flip it back to `TRUE` (or blank) whenever.
- **Add a competitor you forgot** → edit the Competitors cell for that row, comma-separated. Takes effect on the next run — you don't need to re-add the keyword.
- **Only check one platform for a specific keyword** (e.g. save cost, or you only care about ChatGPT for this one) → put `chatgpt` in that row's Platforms cell.

---

## 3. How many keywords/competitors can this handle?

As many as you want — the sheet just grows. Here's the actual math so
scaling isn't a mystery:

**Rows written per day = keywords × platforms × entities per keyword**

Where "entities" = your domain (1) + your brand if filled in (1) + however
many competitors you listed.

**Example**: 10 keywords, each with 5 competitors and a Brand filled in,
checked on both Claude and ChatGPT:

```
10 keywords × 2 platforms × (1 own + 1 brand + 5 competitors)
= 10 × 2 × 7 = 140 rows written per day
```

Google Sheets supports up to 10 million cells per spreadsheet, so even a
year of daily runs at that volume (140 × 365 ≈ 51,000 rows) is nowhere
close to a real limit. The only practical ceiling is Vercel's function time
budget — see [§7](#7-when-things-are-slow-or-time-out).

Every keyword only costs **one AI call per platform**, no matter how many
competitors you attach to it — competitors are found for free by checking
the same search results your domain was checked against. Adding a 6th
competitor to a keyword costs nothing extra; adding an 11th keyword does.

---

## 4. Where results go — the Results tabs

Each AI platform writes to its own tab:

- **Results - Claude**
- **Results - ChatGPT**

Both get created automatically the first time that platform runs — you
don't need to create them yourself.

Every day's run appends new rows to the bottom, with **one blank row before
each new day's block** so you can visually tell where "today" starts when
scrolling. The very first run for a tab has no leading blank (nothing to
separate it from).

Nothing ever gets edited or deleted automatically — this is an
append-only log, so you always have the full history to look back on.

---

## 5. Reading the results tabs

| Column | What it means |
|---|---|
| **Date / Time (IST)** | When that run happened |
| **Project** | Matches the Project from the Keywords tab |
| **Keyword** | The exact query that was checked |
| **Intent** | Auto-classified as `local`, `transactional`, `comparison`, or `informational` — based on the wording of the keyword itself (e.g. "best X" → comparison, "X near me" → local) |
| **Entity Type** | `own` = your domain, `own brand` = your brand name, `competitor` = one of the competitor domains |
| **Entity Domain** | The domain (or brand name, for the `own brand` row) this row is scoring |
| **Rank** | **Text rank — the position this entity appeared at in the AI's actual search results for this query**, 1 being first. Blank means it did not show up in the search results at all for this run. This is a live search position, not a stored/cached ranking — it can move day to day even with nothing changing on your site, because the AI is genuinely searching fresh each time. |
| **Score** | A 0–100 visibility score derived from Rank plus the citation bonus below. Higher rank + being directly cited = higher score. Blank/0 when not found; blank specifically when the AI never searched the web at all for that query (see Grounded). |
| **Cited In Answer** | **Source citation — `yes` if the AI didn't just find this domain in its search results, but actually quoted/referenced it directly in the written answer it gave.** This is the strongest visibility signal: showing up in search results means the AI *saw* you; being cited means the AI *used* you when answering. A domain can rank #1 in results but still show `no` here if the AI's final answer happened to quote a different source. |
| **#1 Result** | Whichever domain came out on top overall for that query, for context |
| **Grounded** | `yes` if the AI actually performed a live web search for this query at all. `no` means it answered from memory without searching — in that case Rank/Score are meaningless and will be blank, because there was no live search to rank against. |
| **Error** | Only filled in if that specific keyword failed to run (rate limit, API issue, etc.) — everything else on that row will be blank when this is filled in |

**The two numbers to actually pay attention to are Rank and Cited In
Answer** — Rank tells you if you're being *found*, Cited tells you if
you're being *used*. A `yes` in Cited In Answer is worth more than a good
Rank with a `no`.

---

## 6. Claude vs. ChatGPT — one real difference

Claude's search results come with both a title and a URL for every result.
ChatGPT's underlying search only exposes URLs for the raw result list (no
titles) — so on `Results - ChatGPT`, the domain name is used in place of a
title where the AI didn't separately provide one. This doesn't affect Rank,
Score, or Cited In Answer accuracy — it only affects how a result's
name is displayed.

---

## 7. When things are slow or time out

Every keyword × platform combination is one live AI call, and those take a
few seconds each. With enough keywords active at once, a single run can
bump into Vercel's function time limit (60 seconds on the free Hobby plan).
If runs start failing or timing out as the Keywords tab grows past
roughly 15–20 active rows, that's the ceiling — ask engineering about
upgrading to Vercel Pro (raises the limit to 300 seconds), not a sheet or
data problem.

---

## 8. Quick FAQ

**Q: I added a keyword an hour ago and don't see it in Results yet.**
A: It'll appear on the next run — either the 9 PM IST scheduled one, or
whenever someone triggers a manual run. Adding a row doesn't run it
immediately by itself.

**Q: A competitor shows "not found" every single day — are they really
invisible to AI search?**
A: More likely than not, yes, for that specific keyword — but confirm with
a few days of data before concluding that, since any single day can be
unlucky. Consistent absence across a week is a much stronger signal than
one day.

**Q: Our own domain dropped from rank 1 to "not found" overnight — did we
get penalized?**
A: Almost certainly just search variance, not a penalty — there's no
concept of an AI "penalty" the way Google has one. Re-run it manually a
couple of times to see if it comes back; if it's consistently gone for
several days running, that's worth investigating on-page/content
reasons, not something to panic over from one bad day.

**Q: Can we track a domain in Claude only, not ChatGPT?**
A: Yes — put `claude` in that row's Platforms column.

**Q: We want to stop tracking a keyword but keep the history.**
A: Set Active to `FALSE`. The row stays, past results stay, it just won't
run again until you flip it back.
