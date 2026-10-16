"""
Local / server entry point (also what the Docker app container runs):

  python -m geo run                              one full audit run (same as the Vercel cron)
  python -m geo sync                             copy sheet settings into the database (no AI calls)
  python -m geo metrics [--days N]               score sentiment + recompute daily metrics
                                                 (default: last METRICS_DAYS days; use a big N to backfill)
  python -m geo make-admin EMAIL                 give a signed-up user full admin access
  python -m geo add-member EMAIL CLIENT [viewer|editor]
                                                 let a client user see (viewer) or manage (editor) a client
  python -m geo remove-member EMAIL CLIENT
  python -m geo invite EMAIL admin               grant admin now, or on first sign-in if not signed up yet
  python -m geo invite EMAIL CLIENT [viewer|editor]
"""
import json
import os
import sys

from geo.config import load_dotenv


def _db_url():
    url = os.environ.get("DATABASE_URL_DIRECT", "").strip() or os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit("DATABASE_URL (or DATABASE_URL_DIRECT) not set.")
    return url


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def main(argv):
    load_dotenv()
    args = argv[1:]
    cmd = args[0] if args else ""

    if cmd == "run":
        from geo.audit import run
        result = run(trigger="manual")
        _print(result)
        return 0 if result.get("ok") else 1

    if cmd == "sync":
        from geo.audit import sync_only
        result = sync_only()
        _print(result)
        return 0 if result.get("ok") else 1

    if cmd == "metrics":
        from geo import metrics
        days = None
        if "--days" in args:
            days = int(args[args.index("--days") + 1])
        result = metrics.run(days=days)
        _print(result)
        return 0 if result.get("ok") else 1

    if cmd in ("make-admin", "add-member", "remove-member", "invite"):
        from geo import admin
        from geo.db import session_scope
        try:
            with session_scope(_db_url()) as s:
                if cmd == "make-admin" and len(args) == 2:
                    admin.set_role(s, args[1], "admin")
                    print(f"{args[1]} is now an admin.")
                elif cmd == "add-member" and len(args) in (3, 4):
                    role = args[3] if len(args) == 4 else "viewer"
                    admin.add_member(s, args[1], args[2], role)
                    print(f"{args[1]} can now access {args[2]!r} as {role}.")
                elif cmd == "invite" and len(args) in (3, 4):
                    if args[2] == "admin":
                        how = admin.invite(s, args[1], "admin")
                    else:
                        how = admin.invite(s, args[1], "client", args[2], args[3] if len(args) == 4 else "viewer")
                    print({"applied": f"{args[1]} already had an account: access granted now.",
                           "invited": f"{args[1]} invited: access is granted on their first sign-in."}[how])
                elif cmd == "remove-member" and len(args) == 3:
                    print("Removed." if admin.remove_member(s, args[1], args[2]) else "Was not a member.")
                else:
                    print(__doc__.strip())
                    return 2
        except admin.AdminError as e:
            print(f"Error: {e}")
            return 1
        return 0

    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
