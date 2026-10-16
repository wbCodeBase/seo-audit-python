#!/usr/bin/env python3
"""
One-off: copy existing "Results - Claude" / "Results - ChatGPT" history from
the Google Sheet into Postgres. Safe to re-run (already-imported runs are
skipped).

  python scripts/import_sheet_history.py --dry-run   # count only, writes nothing
  python scripts/import_sheet_history.py             # import

Needs GOOGLE_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON and DATABASE_URL (or
DATABASE_URL_DIRECT) in .env or the environment, and the tables created
first with:  alembic upgrade head
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geo.config import get_settings, load_dotenv  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="read and group the sheet rows, but write nothing")
    args = ap.parse_args()

    load_dotenv()
    from geo import importer, sheets_io
    from geo.db import session_scope

    settings = get_settings()
    db_url = os.environ.get("DATABASE_URL_DIRECT", "").strip() or settings.database_url
    if not settings.google_sheet_id:
        sys.exit("GOOGLE_SHEET_ID not set.")
    if not db_url and not args.dry_run:
        sys.exit("DATABASE_URL (or DATABASE_URL_DIRECT) not set.")
    svc = sheets_io.sheets_service(settings.google_service_account_json)
    if not svc:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON not set or invalid.")

    titles = set(sheets_io._tab_titles(svc, settings.google_sheet_id))
    answers = []
    for app, tab in importer.APP_TABS.items():
        if tab not in titles:
            print(f"- {tab}: not found, skipping")
            continue
        values = sheets_io._get_rows(svc, settings.google_sheet_id, tab, "A:Z")
        grouped = importer.group_history(values, app)
        print(f"- {tab}: {max(len(values) - 1, 0)} rows -> {len(grouped)} answers")
        answers += grouped

    runs = len({a["run_id"] for a in answers})
    if args.dry_run:
        print(f"Dry run: would import up to {len(answers)} answers across {runs} runs. Nothing written.")
        return

    with session_scope(db_url) as s:
        stats = importer.import_answers(s, answers)
    print(f"Imported {stats['answers']} answers in {stats['runs']} new runs "
          f"({stats['skipped_runs']} runs already imported, skipped).")


if __name__ == "__main__":
    main()
