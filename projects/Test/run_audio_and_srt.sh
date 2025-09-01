#!/usr/bin/env bash
set -euo pipefail
PROJ="projects/Test"

# 音声＆SRTだけ先に生成（プレビュー動画は作らない）
../.venv/bin/python adv_maker.py \
  --project "${PROJ}" \
  --script  "${PROJ}/scripts/script.json" \
  --srt     "${PROJ}/output/subtitles.srt" \
  --mix-wav "${PROJ}/output/mix.wav" \
  --out     "${PROJ}/output/reference.mp4" \
  --audio-only
