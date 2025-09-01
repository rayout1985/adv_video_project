#!/usr/bin/env bash
set -euo pipefail
PROJ="projects/Test"

# DSL -> .osp（BG α=1、アクション無効、各セリフWAVを自動配置、字幕PNG+プレートPNG）
../.venv/bin/python scripts/timeline_to_projects.py \
  --dsl       "${PROJ}/scripts/timeline.txt" \
  --project   "${PROJ}" \
  --adv-out   "${PROJ}/scripts/script.json" \
  --osp-out   "${PROJ}/openshot/project.osp" \
  --fps 30 --width 1920 --height 1080 \
  --path-mode relative \
  --ignore-actions \
  --audio-placement lines \
  --subtitle-font "/mnt/c/Windows/Fonts/meiryo.ttc" \
  --subtitle-font-size 60 \
  --subtitle-bottom 94 \
  --plate-image "assets/ui/plate.png"
