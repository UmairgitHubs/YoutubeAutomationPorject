FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend
COPY episodes /app/episodes-seed

WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "mkdir -p /data/logs /data/episodes && if [ ! -f /data/.seeded ] && [ -d /app/episodes-seed ]; then cp -a /app/episodes-seed/. /data/episodes/ && touch /data/.seeded; fi && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8088}"]
