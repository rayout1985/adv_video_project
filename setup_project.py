# -*- coding: utf-8 -*-
"""
WSL向け 完全版セットアップ & プロジェクト雛形展開ツール
VOICEVOX Engine 自動起動対応 (デフォルトで有効)

使い方:
  # 依存導入/アップデート
  python3 setup_project.py

  # 仮想環境を作り直し（クリーン再構築）
  python3 setup_project.py --reinstall

  # 新規動画プロジェクトを作成（projects/MyTitle）
  python3 setup_project.py --init-project "MyTitle"
"""
import os
import sys
import json
import argparse
import subprocess
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent

def run(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd))
    subprocess.check_call(cmd, **kw)

def ensure_ffmpeg_hint():
    # ffmpeg は moviepy で必要。未導入ならヒントだけ出す
    try:
        subprocess.check_call(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print("\n[Hint] ffmpeg が見つかりません。Ubuntuでは以下で導入してください:")
        print("  sudo apt-get update && sudo apt-get install -y ffmpeg\n")

def create_or_recreate_venv(reinstall: bool):
    venv_dir = BASE / ".venv"
    if reinstall and venv_dir.exists():
        print("[Reinstall] remove .venv ...")
        shutil.rmtree(venv_dir)

    if not venv_dir.exists():
        run([sys.executable, "-m", "venv", ".venv"], cwd=str(BASE))

    pip = venv_dir / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
    run([str(pip), "install", "-U", "pip", "wheel"], cwd=str(BASE))
    run([str(pip), "install", "-r", "requirements.txt"], cwd=str(BASE))
    ensure_ffmpeg_hint()
    return venv_dir

def write_file(path: Path, content: str, binary=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if binary else "w"
    with open(path, mode, encoding=None if binary else "utf-8") as f:
        f.write(content)

# プロジェクトのサンプル台本
TEMPLATE_SCRIPT_BASIC = [
  {
    "bg": "assets/bg/bg.png",
    "lines": [
      {
        "character": "assets/chars/char_left.png",
        "position": "left",
        "voice": 3,
        "text": "こんにちは、今日は良い天気ですね。",
        "action": [
          {"type": "fadein", "start": 0.0, "duration": 0.6, "easing": "ease_in_out"},
          {"type": "zoomin", "start": 0.0, "duration": 0.0, "strength": 0.3}
        ]
      },
      {
        "character": "assets/chars/char_right.png",
        "position": "right",
        "voice": 8,
        "text": "うん、散歩に行きたくなってきた！",
        "action": [
          {"type": "jump", "start": 0.2, "duration": 0.6, "strength": 1.0},
          {"type": "fadein", "start": 0.0, "duration": 0.4}
        ]
      }
    ]
  }
]

def copy_placeholders(dst_root: Path):
    """既存の assets からダミー素材をコピー（存在するものだけ）"""
    src_assets = BASE / "assets"
    dst_assets = dst_root / "assets"
    for sub in ["bg", "chars", "eyecatch", "audio"]:
        s = src_assets / sub
        d = dst_assets / sub
        if s.exists():
            d.mkdir(parents=True, exist_ok=True)
            for item in s.iterdir():
                if item.is_file():
                    shutil.copy2(item, d / item.name)

def init_project(name: str, dst: Path):
    proj_dir = (dst / name).resolve()
    # ディレクトリ構成
    paths = [
        proj_dir / "assets" / "bg",
        proj_dir / "assets" / "chars",
        proj_dir / "assets" / "eyecatch",
        proj_dir / "assets" / "audio",
        proj_dir / "scripts",
        proj_dir / "voices",
        proj_dir / "output",
    ]
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)

    # サンプル台本
    write_file(proj_dir / "scripts" / "script.json",
               json.dumps(TEMPLATE_SCRIPT_BASIC, ensure_ascii=False, indent=2))

    # ダミー素材コピー
    copy_placeholders(proj_dir)

    # ランナースクリプト（Linux & Windows）
    run_sh = f"""#!/bin/bash
# Auto-generated runner for project '{name}'
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

SCRIPT_REL="${{SCRIPT_REL:-scripts/script.json}}"
OUT_MP4="${{OUT_MP4:-output/${{PROJECT_NAME}}.mp4}}"
OUT_SRT="${{OUT_SRT:-output/${{PROJECT_NAME}}.srt}}"

exec "$PY" "$ROOT_DIR/adv_maker.py" \\
  --project "$PRJ_DIR" \\
  --script "$SCRIPT_REL" \\
  --out "$OUT_MP4" \\
  --srt "$OUT_SRT"
"""
    run_bat = f"""@echo off
REM Auto-generated runner for project '{name}'
setlocal EnableDelayedExpansion

set PRJ_DIR=%~dp0
set PRJ_DIR=%PRJ_DIR:~0,-1%
for %%I in ("%PRJ_DIR%") do set PROJECT_NAME=%%~nI
for %%I in ("%PRJ_DIR%\\..\\..") do set ROOT_DIR=%%~fI

set PY_WIN=%ROOT_DIR%\\.venv\\Scripts\\python.exe
if exist "%PY_WIN%" (
  set PY_CMD="%PY_WIN%"
) else (
  set PY_CMD=python
)

if "%SCRIPT_REL%"=="" set SCRIPT_REL=scripts\\script.json
if "%OUT_MP4%"=="" set OUT_MP4=output\\%PROJECT_NAME%.mp4
if "%OUT_SRT%"=="" set OUT_SRT=output\\%PROJECT_NAME%.srt

%PY_CMD% "%ROOT_DIR%\\adv_maker.py" ^
  --project "%PRJ_DIR%" ^
  --script "%SCRIPT_REL%" ^
  --out "%OUT_MP4%" ^
  --srt "%OUT_SRT%"
"""

    sh_path = proj_dir / "run_project.sh"
    bat_path = proj_dir / "run_project.bat"
    write_file(sh_path, run_sh)
    write_file(bat_path, run_bat)

    try:
        st = os.stat(sh_path).st_mode
        os.chmod(sh_path, st | 0o111)
    except Exception:
        pass

    readme = f"""# Project: {name}

このフォルダは、動画プロジェクト単位の素材と出力をまとめます。

## 実行
- Linux/WSL: `./run_project.sh`
- Windows: `run_project.bat`
"""
    write_file(proj_dir / "README.md", readme)

    print(f"[OK] プロジェクト雛形を作成しました: {proj_dir}")
    print("  - run_project.sh / run_project.bat を生成しました")
    return proj_dir

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reinstall", action="store_true", help="仮想環境を作り直して依存を再インストール")
    ap.add_argument("--init-project", help="新規動画プロジェクト名（projects/配下に作成）")
    ap.add_argument("--dst", default="projects", help="プロジェクトの展開先ディレクトリ")
    args = ap.parse_args()

    create_or_recreate_venv(reinstall=args.reinstall)

    if args.init_project:
        dst = (BASE / args.dst)
        dst.mkdir(parents=True, exist_ok=True)
        init_project(args.init_project, dst)
    else:
        print("\n[Setup] 依存の導入が完了しました。")
        print("VOICEVOX Engine は未起動なら adv_maker.py 実行時に自動起動されます。")
        print("新規動画プロジェクトを作るには:")
        print("  python3 setup_project.py --init-project MyTitle\n")

if __name__ == "__main__":
    main()
