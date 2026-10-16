"""
geo — shared core for the AI visibility (GEO) audit.

Everything the daily audit needs that isn't HTTP plumbing lives here so the
Vercel cron handler (api/scheduled_audit.py), local CLI runs, the history
import script and — in later phases — the job-queue workers and REST API all
share one implementation instead of copy-pasted helpers.
"""
