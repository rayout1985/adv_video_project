#!/bin/bash
# Auto-generated runner for project 'Test'
set -euo pipefail
PRJ_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PRJ_DIR/../.." && pwd)"
PROJECT_NAME="$(basename "$PRJ_DIR")"

PY_UNIX="$ROOT_DIR/.venv/bin/python"
PY_WIN="$ROOT_DIR/.venv/Scripts/python.exe"

if [ -x "$PY_UNIX" ]; then
  PY="$PY_UNIX"
elif [ -f "$PY_WIN" ]; then
  if command -v python3 >/dev/null 2>&1; then PY="python3"; else PY="python"; fi
else
  if command -v python3 >/dev/null 2>&1; then PY="python3"; else PY="python"; fi
fi

SCRIPT_REL="${SCRIPT_REL:-scripts/script.json}"
OUT_MP4="${OUT_MP4:-output/${PROJECT_NAME}.mp4}"
OUT_SRT="${OUT_SRT:-output/${PROJECT_NAME}.srt}"

exec "$PY" "$ROOT_DIR/adv_maker.py" \
  --project "$PRJ_DIR" \
  --script "$SCRIPT_REL" \
  --out "$OUT_MP4" \
  --srt "$OUT_SRT"
