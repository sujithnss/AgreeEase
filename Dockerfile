# Docker build so we can install LibreOffice (for docx->pdf conversion in
# backend/docgen.py) — Render's native Python runtime build environment is
# sandboxed/read-only and can't apt-get install anything.
FROM python:3.13-slim

# fonts-noto pulls in Noto Sans Malayalam (among others), which the
# rental_agreement_ml.docx template's Malayalam text relies on — without it
# LibreOffice renders Malayalam glyphs as tofu boxes in the converted PDF.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice \
        fonts-noto \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY . .

# db.py and main.py resolve some paths (backend/app.db, backend/generated/)
# relative to the process's working directory, so the app must be started
# from backend/, same as the old `rootDir: backend` native-runtime setup.
WORKDIR /app/backend

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
