# -*- coding: utf-8 -*-
"""
ADV style video builder

- JSONスクリプト（bg / lines[]）から動画を生成
- VOICEVOXで音声合成 → WAVを敷いて動画合成
- レイヤー順: 背景 < キャラ < セリフ用フレーム（半透明）
- SRT字幕を同時出力
- VOICEVOX自動起動（config: ENGINE_AUTO_START=True時）
- MoviePy 1.x / 2.x 両対応

CLI:
  python adv_maker.py \
    --project projects/Test \
    --script scripts/script.json \
    --out output/Test.mp4 \
    --srt output/Test.srt \
    [--vvurl http://172.30.xx.1:50021]

スクリプトJSON例:
[
  {
    "bg": "assets/bg/bg.png",
    "lines": [
      {
        "character": "assets/chars/char_left.png",
        "position": "left",
        "voice": 3,
        "text": "こんにちは！",
        "action": [
          {"type": "fadein", "start": 0.0, "duration": 0.6, "easing": "ease_in_out"},
          {"type": "jump", "start": 0.2, "duration": 0.6, "strength": 1.0}
        ]
      },
      ...
    ]
  }
]
"""
import os
import json
import argparse
from pathlib import Path
from typing import List, Tuple, Optional

# ==== MoviePy 両対応 import ====
try:
    # MoviePy < 2.0
    from moviepy.editor import (
        ImageClip, CompositeVideoClip, AudioFileClip,
        CompositeAudioClip, ColorClip, TextClip  # TextClipは未使用でも将来用
    )
except Exception:
    # MoviePy >= 2.0
    from moviepy import (
        ImageClip, CompositeVideoClip, AudioFileClip,
        CompositeAudioClip, ColorClip, TextClip, vfx
    )

# ==== 自前モジュール ====
from config.config import (
    VOICEVOX_BASE_URL, DEFAULT_CANVAS, LAYOUT, AUDIO_PADDING_SEC, EYE_CATCH,
    # 自動起動関連（無ければダミー扱い）
    ENGINE_AUTO_START, ENGINE_EXE_PATH, ENGINE_WORKDIR, ENGINE_ARGS, ENGINE_BOOT_TIMEOUT_SEC,
)
# スピーカー辞書（任意）
try:
    from config.config import SPEAKERS  # noqa: F401
except Exception:
    SPEAKERS = {}

# VOICEVOX クライアント（class版 / 旧関数版の両対応）
VV_CLIENT_CLASS = None
vv_health_check = None
tts_to_wav_func = None
try:
    from scripts.voicevox_client import VoicevoxClient as VV_CLIENT_CLASS  # class版
except Exception:
    VV_CLIENT_CLASS = None

if VV_CLIENT_CLASS is None:
    try:
        # 旧互換
        from scripts.voicevox_client import tts_to_wav as tts_to_wav_func, health_check as vv_health_check
    except Exception:
        pass

# 自動起動ヘルパ（任意）
start_if_needed = None
try:
    from scripts.engine_autostart import start_if_needed as _start_if_needed
    start_if_needed = _start_if_needed
except Exception:
    start_if_needed = None

# アクション適用
from scripts.actions import apply_actions


# ====== ユーティリティ ======
def ensure_exists(path: Path, kind="file"):
    if kind == "file":
        if not path.is_file():
            raise FileNotFoundError(f"Missing file: {path}")
    else:
        if not path.exists():
            raise FileNotFoundError(f"Missing path: {path}")

def to_px(pos_key: str, W: int, H: int, base_scale: float) -> Tuple[int, int, float]:
    """LAYOUTの比率設定からpx座標へ"""
    lay = LAYOUT.get(pos_key, LAYOUT.get("left", {"x": 0.15, "y": 0.6, "scale": 1.0}))
    x = int(W * float(lay.get("x", 0.15)))
    y = int(H * float(lay.get("y", 0.6)))
    scale = float(lay.get("scale", 1.0)) * base_scale
    return x, y, scale

def srt_ts(sec: float) -> str:
    if sec < 0: sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def write_srt(entries: List[Tuple[int, float, float, str]], out_path: Path):
    # entries: (idx, start, end, text)
    lines = []
    for idx, st, ed, txt in entries:
        lines.append(f"{idx}")
        lines.append(f"{srt_ts(st)} --> {srt_ts(ed)}")
        lines.append(txt)
        lines.append("")  # blank
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ====== メインビルド ======
def build(script_path: str, out_mp4: str, out_srt: str, project_root: str = "", vv_url: str = ""):
    SAFE_EPS = 0.02  # WAV境界読み超え防止の安全マージン(秒)
    # ---- Paths ----
    proj = Path(project_root).resolve() if project_root else Path.cwd()
    scr_path = (proj / script_path).resolve()
    out_mp4_p = (proj / out_mp4).resolve()
    out_srt_p = (proj / out_srt).resolve()
    voices_dir = (proj / "voices").resolve()
    voices_dir.mkdir(parents=True, exist_ok=True)

    # ---- Config ----
    W, H, FPS = DEFAULT_CANVAS["width"], DEFAULT_CANVAS["height"], DEFAULT_CANVAS["fps"]

    # ---- Load script ----
    ensure_exists(scr_path, "file")
    scenelist = json.loads(scr_path.read_text(encoding="utf-8"))

    # ---- VOICEVOX client ----
    base_url = vv_url or os.environ.get("VOICEVOX_BASE_URL", VOICEVOX_BASE_URL)

    vv = None
    if VV_CLIENT_CLASS is not None:
        vv = VV_CLIENT_CLASS(base_url=base_url, retries=2, backoff=0.8)

        # 到達不可なら自動起動
        ok = vv.version_ok()
        if not ok and ENGINE_AUTO_START and start_if_needed is not None:
            ok = start_if_needed(base_url, ENGINE_EXE_PATH, ENGINE_WORKDIR, ENGINE_ARGS, timeout_sec=ENGINE_BOOT_TIMEOUT_SEC)
        if not ok:
            raise RuntimeError(f"VOICEVOX not reachable at {base_url}. Check engine, IP/port, and firewall.")
    else:
        # 旧関数版
        if vv_health_check is not None:
            ok = vv_health_check(base_url)
            if not ok and ENGINE_AUTO_START and start_if_needed is not None:
                ok = start_if_needed(base_url, ENGINE_EXE_PATH, ENGINE_WORKDIR, ENGINE_ARGS, timeout_sec=ENGINE_BOOT_TIMEOUT_SEC)
            if not ok:
                raise RuntimeError(f"VOICEVOX not reachable at {base_url}. Check engine, IP/port, and firewall.")
        else:
            raise RuntimeError("voicevox_client not available.")

    # ---- Eyecatch (optional, 最初に入れる) ----
    layers = []
    audio_layers = []
    srt_entries = []
    timeline_t = 0.0  # 全体タイムライン（秒）

    if EYE_CATCH and isinstance(EYE_CATCH, dict):
        ec_img = EYE_CATCH.get("image")
        ec_dur = float(EYE_CATCH.get("duration", 0.0) or 0.0)
        if ec_img and ec_dur > 0:
            ec_path = (proj / ec_img).resolve()
            ensure_exists(ec_path, "file")
            ec = ImageClip(str(ec_path)).resize((W, H)).set_duration(ec_dur).set_start(timeline_t)
            layers.append(ec)
            timeline_t += ec_dur

    # ---- Iterate scenes ----
    line_idx = 0
    for scene in scenelist:
        bg_path = (proj / scene["bg"]).resolve()
        ensure_exists(bg_path, "file")

        # 背景はこのシーンの合計長だけ表示するため、まず line を先に処理して長さを知る
        # ただし、重複処理を避けるため、一旦行ごとに作って start/end を記録 → bgを最後に追加
        scene_line_clips = []
        scene_audio_clips = []
        scene_srt_entries = []

        scene_t0 = timeline_t
        cursor = timeline_t

        for ln in scene.get("lines", []):
            line_idx += 1
            text = str(ln.get("text", ""))
            char_img_rel = ln.get("character")
            pos_key = ln.get("position", "left")
            speaker = int(ln.get("voice", 0) or 0)
            actions = ln.get("action", None)

            char_path = (proj / char_img_rel).resolve()
            ensure_exists(char_path, "file")

            # ---- TTS → wav ----
            wav_path = voices_dir / f"line_{line_idx:03d}.wav"
            if VV_CLIENT_CLASS is not None and vv is not None:
                real_dur = vv.synthesize_to_wav(text, speaker, str(wav_path))
            else:
                real_dur = tts_to_wav_func(base_url, text, speaker, str(wav_path))  # type: ignore

            # WAV 実長を安全に使う
            af = AudioFileClip(str(wav_path))
            # 実長（いったんreaderに依存）
            real_dur = float(af.duration or real_dur or 0.0)

            # 末尾の安全カット
            safe_tail = max(0.0, real_dur - SAFE_EPS)
            af = af.subclip(0, safe_tail)

            # Audio の開始を設定
            af = af.set_start(cursor)
            scene_audio_clips.append(af)

            # ---- Visual: character ----
            # 位置と基準スケール
            x_px, y_px, base_scale = to_px(pos_key, W, H, base_scale=1.0)

            ch = ImageClip(str(char_path)).set_duration(safe_tail)
            ch = apply_actions(
                ch_clip=ch,
                line_start=0.0,                    # 関数内では相対t=0起点で処理
                line_duration=safe_tail,
                action_spec=actions,
                base_pos_xy=(x_px, y_px),
                base_scale=base_scale,
                FPS=FPS
            ).set_start(cursor)

            scene_line_clips.append(ch)

            # ---- Subtitle frame（半透明帯）----
            # 画面下部に帯を出す（読みやすさ向上）
            frame_h = int(H * 0.18)
            frame_y = H - frame_h
            subtitle_frame = ColorClip(size=(W, frame_h), color=(0, 0, 0)).set_opacity(0.35)
            subtitle_frame = subtitle_frame.set_duration(safe_tail).set_start(cursor).set_position((0, frame_y))
            scene_line_clips.append(subtitle_frame)

            # ---- SRT ----
            st = cursor
            ed = cursor + safe_tail
            scene_srt_entries.append((line_idx, st, ed, text))

            # 次の行の開始時刻
            cursor = ed + AUDIO_PADDING_SEC

        # シーン全体の長さ
        scene_len = cursor - scene_t0
        # 背景を追加（scene期間）
        bg = ImageClip(str(bg_path)).resize((W, H)).set_duration(scene_len).set_start(scene_t0)
        layers.append(bg)
        # シーンの映像と音声・SRTを全体に追加
        layers.extend(scene_line_clips)
        audio_layers.extend(scene_audio_clips)
        srt_entries.extend(scene_srt_entries)

        # タイムライン更新
        timeline_t = scene_t0 + scene_len

    # ---- 合成 & 出力 ----
    comp = CompositeVideoClip(layers, size=(W, H))
    if audio_layers:
        comp.audio = CompositeAudioClip(audio_layers)

    out_mp4_p.parent.mkdir(parents=True, exist_ok=True)
    out_srt_p.parent.mkdir(parents=True, exist_ok=True)

    # SRT書き出し
    write_srt(srt_entries, out_srt_p)

    # 動画書き出し
    comp.write_videofile(
        str(out_mp4_p),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str((out_mp4_p.parent / (out_mp4_p.stem + "_TEMP_MPY_wvf_snd.mp4")).resolve()),
        remove_temp=True,
        threads=os.cpu_count() or 4,
        preset="medium"
    )


# ====== CLI ======
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="", help="プロジェクトのルート（projects/YourTitle など）")
    ap.add_argument("--script", default="scripts/script.json", help="台本JSONへの相対パス（project基準）")
    ap.add_argument("--out", default="output/out.mp4", help="出力mp4（project基準）")
    ap.add_argument("--srt", default="output/out.srt", help="出力srt（project基準）")
    ap.add_argument("--vvurl", default="", help="VOICEVOX base URL を上書き (例: http://172.30.80.1:50021)")
    args = ap.parse_args()

    build(args.script, args.out, args.srt, project_root=args.project, vv_url=args.vvurl)


if __name__ == "__main__":
    main()
