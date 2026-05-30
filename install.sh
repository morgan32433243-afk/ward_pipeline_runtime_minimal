#!/usr/bin/env bash
set -euo pipefail

WARD_ROOT="${WARD_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$WARD_ROOT"

PYTHON_BIN=""
if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.11)"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "Python 3.11 is required." >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required. Install it and ensure it is on PATH." >&2
  exit 1
fi

echo "Creating virtual environment in $WARD_ROOT/.venv"
"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate

echo "Installing Python dependencies"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "Installing Playwright Chromium for OpenEvidence browser mode"
python -m playwright install chromium

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

mkdir -p data/incoming_audio data/output data/logs data/case_views data/obsidian_vault data/stt_review_queue data/ward_audio_archive

echo "Install complete."
echo "Edit $WARD_ROOT/.env, then run: .venv/bin/python ward_cli.py config"
