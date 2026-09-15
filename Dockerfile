# Intelligent Land Record Digitization & Validation System (DILRMP)
# Hosted deployment image (Render / any Docker host).
#
# Build context = repository root; the application lives in extracted/.
#
#   docker build -t landrec .
#   docker run -p 10000:10000 landrec

FROM python:3.11-slim

# Memory hardening for 512MB hosts (Render free tier):
#  * PYTHONMALLOC=malloc + MALLOC_ARENA_MAX=2 keep glibc from bloating
#    with many arenas (big savings in multithreaded Python)
#  * the app is PIL-only (no OpenCV) precisely so server + OCR worker +
#    Tesseract fit inside 512MB — OpenCV alone cost ~200MB and OOM-killed
#    the worker on the free tier
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONMALLOC=malloc \
    MALLOC_ARENA_MAX=2

# Tesseract OCR + all 11 Indic language packs + a Devanagari font for PDFs
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-ben tesseract-ocr-eng tesseract-ocr-guj tesseract-ocr-hin \
    tesseract-ocr-kan tesseract-ocr-mal tesseract-ocr-ori tesseract-ocr-pan \
    tesseract-ocr-tam tesseract-ocr-tel tesseract-ocr-urd \
    fonts-lohit-deva \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY extracted/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY extracted/ .

# Render (and most PaaS) inject the assigned port via $PORT
EXPOSE 10000

# Seed demo data only when the database is empty (free-tier disks are
# ephemeral — every boot gets a fresh, fully-working demo), then serve.
CMD ["sh", "-c", "python3 seed_demo_data.py && python3 -m uvicorn landrec.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
