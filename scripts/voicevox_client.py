# -*- coding: utf-8 -*-
"""
VOICEVOX HTTP クライアント（安定版）
- /version ヘルスチェック
- audio_query → synthesis の合成
- WAV 書き出し & 実長(秒)の返却
- リトライ/タイムアウト付き
- 旧実装互換ラッパ: tts_to_wav(), health_check()
"""

from __future__ import annotations
import json
import time
import wave
import contextlib
import os
from typing import Dict, Optional

import requests

# デフォルトのタイムアウト (connect, read)
DEFAULT_TIMEOUT = (5, 30)
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 0.8  # s, attemptごとに倍加

class VoicevoxClient:
    def __init__(
        self,
        base_url: str,
        speakers: Optional[Dict[str, int]] = None,
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        timeout=DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.speakers = speakers or {}
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout

    # ---- health ----
    def version_ok(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/version", timeout=self.timeout)
            return r.ok
        except Exception:
            return False

    # ---- low-level ----
    def _audio_query(self, text: str, speaker_id: int) -> dict:
        r = requests.post(
            f"{self.base_url}/audio_query",
            params={"text": text, "speaker": speaker_id},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def _synthesis(self, query: dict, speaker_id: int) -> bytes:
        # VOICEVOX 0.24.x では json= でOK
        r = requests.post(
            f"{self.base_url}/synthesis",
            params={"speaker": speaker_id},
            json=query,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.content

    # ---- high-level ----
    def synthesize(self, text: str, speaker_id: int) -> Optional[bytes]:
        last_err = None
        for attempt in range(self.retries + 1):
            try:
                q = self._audio_query(text, speaker_id)
                audio = self._synthesis(q, speaker_id)
                return audio
            except requests.exceptions.RequestException as e:
                last_err = e
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
        print("❌ VOICEVOX Engine に接続できません。BASE_URL/ポート/起動状態を確認してください。")
        print(f"   現在の BASE_URL: {self.base_url} / 原因: {repr(last_err)}")
        return None

    def synthesize_to_wav(self, text: str, speaker_id: int, out_path: str) -> float:
        """
        音声を WAV で out_path に保存し、実長(秒)を返す。
        """
        audio = self.synthesize(text, speaker_id)
        if audio is None:
            raise RuntimeError("VOICEVOX synthesis failed")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(audio)
        with contextlib.closing(wave.open(out_path, "rb")) as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate)


# ===== 旧実装互換のラッパ =====
def health_check(base_url: str) -> bool:
    return VoicevoxClient(base_url=base_url).version_ok()

def tts_to_wav(base_url: str, text: str, speaker: int, out_path: str) -> float:
    """
    旧 adv_maker 互換の関数。実長(秒)を返す。
    """
    return VoicevoxClient(base_url=base_url).synthesize_to_wav(text, speaker, out_path)
