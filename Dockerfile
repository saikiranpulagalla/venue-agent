FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp/venue-home

RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-impress fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /tmp/venue-home venueagent

WORKDIR /app
COPY backend /app/backend
COPY reference /app/reference
COPY demo /app/demo
COPY frontend/static /app/frontend/static
RUN pip install --no-cache-dir -e '/app/backend[agent]' \
    && chown -R venueagent:venueagent /tmp/venue-home

WORKDIR /app/backend
USER venueagent
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=15s --start-period=15s --retries=3 \
  CMD python -c "import json,os,urllib.request; p=os.getenv('PORT','8000'); d=json.load(urllib.request.urlopen(f'http://127.0.0.1:{p}/health/deep-readiness',timeout=12)); raise SystemExit(0 if d.get('status')=='ready' else 1)"

CMD ["sh","-c","exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --no-access-log"]
