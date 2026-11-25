# Docker image for the LEDGER Discord bot and local, offline Tesseract OCR.
# Railway detects this Dockerfile automatically at the repository root.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/data/ledger.sqlite3

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY bot.py storage.py ocr_verifier.py squad_ocr.py operation_ocr.py blacksite_lore.py contract_objectives.py blacksite_ids.py blacksite_ui.py player_links.py mentorship.py start_railway.py ./
COPY assets/ ./assets/

# The Railway volume is mounted to /data at runtime, NOT build time.
# Create the directory here only for non-Railway Docker testing.
RUN mkdir -p /data \
    && tesseract --version \
    && tesseract --list-langs

CMD ["python", "-u", "start_railway.py"]
