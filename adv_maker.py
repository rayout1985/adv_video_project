# -*- coding: utf-8 -*-
"""
adv_maker.py – script.json の台詞を VOICEVOX で wav 出力
Usage:
  ./.venv/bin/python scripts/adv_maker.py \
    --project projects/Test \
    --script  projects/Test/scripts/script.json \
    --outdir  projects/Test/voices \
    --vvurl  "http://172.30.80.1:50021"
"""

from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any

import requests

# ---- コンフィグ（SPEAKERS） ----
try:
    from scripts.config import SPEAKERS, SPEAKER_ID_TO_NAME, resolve_speaker_id
except Exception:
    # フォールバック（最低限）
    SPEAKERS = {"ずんだもん": 3, "四国めたん": 2, "春日部つむぎ": 8}
    SPEAKER_ID_TO_NAME = {v: k for k, v in SPEAKERS.items()}

    def resolve_speaker_id(name_or_id: str | int | None, default_name: str = "ずんだもん") -> int:
        if name_or_id is None:
            return SPEAKERS.get(default_name, 3)
        if isinstance(name_or_id, int):
            return name_or_id
        s = str(name_or_id).strip()
        if s.isdigit():
            return int(s)
        return SPEAKERS.get(s, SPEAKERS.get(default_name, 3))


# ---- VOICEVOX クライアント ----
class VoicevoxClient:
    def __init__(self, base_url: str, retries: int = 3, backoff: float = 0.8, timeout_query: float = 10.0, timeout_synth: float = 40.0):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.backoff = backoff
        self.timeout_query = timeout_query
        self.timeout_synth = timeout_synth
        self.sess = requests.Session()

    def synthesize(self, text: str, speaker_id: int) -> Optional[bytes]:
        last_err = None
        for attempt in range(self.retries + 1):
            try:
                q = self.sess.post(
                    f"{self.base_url}/audio_query",
                    params={"text": text, "speaker": speaker_id},
                    timeout=self.timeout_query,
                )
                q.raise_for_status()
                s = self.sess.post(
                    f"{self.base_url}/synthesis",
                    params={"speaker": speaker_id},
                    json=q.json(),
                    timeout=self.timeout_synth,
                )
                s.raise_for_status()
                return s.content
            except requests.RequestException as e:
                last_err = e
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                else:
                    print("❌ VOICEVOX Engine に接続できません。BASE_URL/ポート/起動状態を確認してください。")
                    print(f"   BASE_URL: {self.base_url} / 原因: {repr(e)}")
        return None


# ---- script.json の line を列挙 ----
def iter_lines(script_json: Any):
    """
    script.json 構造（本プロジェクトの想定）
    [
      { "bg": "...", "lines": [
          {"character":"assets/chars/...", "position":"left|right", "text":"...", "speaker":"ずんだもん" }
      ]},
      ...
    ]
    """
    for scene in script_json:
        for line in scene.get("lines", []):
            if "text" in line:
                yield {
                    "text": line["text"],
                    "speaker": line.get("speaker"),  # 日本語名 or 数値 or None
                }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--vvurl", default="http://127.0.0.1:50021")
    ap.add_argument("--default-speaker", default="ずんだもん", help="未指定時の既定話者名（SPEAKERSキー）")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    data = json.loads(Path(args.script).read_text(encoding="utf-8"))

    client = VoicevoxClient(args.vvurl)

    n = 0
    for i, info in enumerate(iter_lines(data), start=1):
        text = str(info["text"])
        spk_token = info.get("speaker")
        style_id = resolve_speaker_id(spk_token, default_name=args.default_speaker)

        wav = client.synthesize(text, style_id)
        if wav is None:
            raise SystemExit(2)

        (outdir / f"line_{i:03d}.wav").write_bytes(wav)
        n += 1
        print(f"[{i:03d}] speaker_id={style_id} text={text[:24]}{'...' if len(text)>24 else ''}")

    print(f"✅ generated {n} wav files at: {outdir}")

if __name__ == "__main__":
    main()
