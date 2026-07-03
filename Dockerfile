FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# psycopg2-binary is used, so no libpq-dev build step is needed; ffmpeg is required by the audio pipeline.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/local_storage

# Render (and most PaaS) inject $PORT; default to 8000 for local/other hosts.
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT','8000'))" || exit 1

# Start the server bound to the platform-provided $PORT. The app creates its schema
# on startup via Base.metadata.create_all() (see app/main.py); Alembic migrations here
# are incremental patches, not a from-scratch builder, so we do NOT run them on boot.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
