# Production image for the Python side: every api/*.py endpoint + public/ UI,
# served by server.py. Built by docker-compose.prod.yml (context = repo root).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Code only — secrets come from env_file at runtime, never baked in.
COPY server.py alembic.ini ./
COPY geo/ geo/
COPY api/ api/
COPY public/ public/
COPY alembic/ alembic/
COPY scripts/ scripts/

RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"

CMD ["python", "server.py"]
