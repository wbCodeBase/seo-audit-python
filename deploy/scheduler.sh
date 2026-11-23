#!/bin/sh
# Replaces Vercel Cron on the VPS. Times are UTC:
#   15:30 UTC = 21:00 IST  nightly GEO audit   (web /api/cron/daily → api jobs)
#   16:30 UTC = 22:00 IST  metrics roll-up     (api /api/compute_metrics)
# busybox crond doesn't pass the container's env to jobs, so the secret is
# written into the crontab when the container starts (it never leaves the box).
set -eu
: "${CRON_SECRET:?CRON_SECRET must be set}"
apk add --no-cache curl >/dev/null

AUTH="Authorization: Bearer ${CRON_SECRET}"
cat > /etc/crontabs/root <<EOF
30 15 * * * curl -fsS -m 3600 -H "${AUTH}" http://web:3000/api/cron/daily > /proc/1/fd/1 2>&1
30 16 * * * curl -fsS -m 1800 -H "${AUTH}" http://api:8000/api/compute_metrics > /proc/1/fd/1 2>&1
EOF
chmod 600 /etc/crontabs/root
echo "scheduler: daily audit 15:30 UTC, metrics 16:30 UTC"
exec crond -f -l 8
