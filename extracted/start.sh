#!/usr/bin/env bash
# Land Record Digitization System — launcher for macOS / Linux
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  Intelligent Land Record Digitization & Validation System"
echo "============================================================"
echo

# 1. Python check
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 not found. Install Python 3.9+ first."
  exit 1
fi

# 2. Tesseract check
if ! command -v tesseract >/dev/null 2>&1; then
  echo "[WARNING] tesseract not found. OCR will not work."
  echo "          macOS:  brew install tesseract tesseract-lang"
  echo "          Debian/Ubuntu: sudo apt-get install tesseract-ocr tesseract-ocr-hin"
  echo
fi

# 3. Virtual env + deps
if [ ! -d ".venv" ]; then
  echo "[1/3] Creating virtual environment..."
  python3 -m venv .venv
fi
echo "[2/3] Installing dependencies..."
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt

# 4. Start
echo "[3/3] Starting server at http://localhost:8000"
echo "  Admin login: admin@landrec.gov.in / Admin@123"
echo
./.venv/bin/python -m uvicorn landrec.main:app --host 0.0.0.0 --port 8000
