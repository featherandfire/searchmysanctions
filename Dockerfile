FROM python:3.11-slim AS builder

WORKDIR /app

# Build deps for psycopg-binary and pandas wheels (most resolve to prebuilt wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim

WORKDIR /app

# Runtime deps only (libpq for psycopg)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# App code
COPY app.py data.py cache.py db.py settings.py sanctions.py gunicorn.conf.py ./
COPY routes ./routes
COPY templates ./templates
COPY static ./static

ENV PORT=8080
EXPOSE 8080

# Health check hits the index — same payload the smoke tests assert against
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:8080/healthz > /dev/null || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
