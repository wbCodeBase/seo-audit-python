FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# Only the code the app needs — secrets (.env) are never copied into the
# image; docker-compose passes them in at runtime via env_file.
COPY geo/ geo/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/
COPY tests/ tests/

# Default: apply migrations and exit. Run an audit with
#   docker compose run --rm app python -m geo run
CMD ["alembic", "upgrade", "head"]
