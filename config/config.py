# -*- coding: utf-8 -*-
"""
ADV動画ジェネレーター用の設定ファイル（WSL + VOICEVOX）
"""

# --- VOICEVOX API の基本URL（--vvurl / 環境変数 VOICEVOX_BASE_URL で上書き可） ---
VOICEVOX_BASE_URL = "http://172.30.80.1:50021"

# --- キャンバス設定 ---
DEFAULT_CANVAS = {"width": 1920, "height": 1080, "fps": 30}
AUDIO_PADDING_SEC = 0.05  # 各行の音声末尾に少し余白

# --- 画面レイアウト（比率で指定: 0.0〜1.0）---
LAYOUT = {
    "left":  {"x": 0.15, "y": 0.60, "scale": 1.0},
    "right": {"x": 0.85, "y": 0.60, "scale": 1.0},
    # 追加で "center" など増やしてOK
}

# --- アイキャッチ（未使用なら None）---
EYE_CATCH = {
    "image": "assets/eyecatch/eyecatch.png",  # 置き換え可（存在しない場合はスキップ）
    "duration": 1.5
}

# ============ Engine 起動設定（デフォルトで自動起動ON） =============
ENGINE_AUTO_START = True
ENGINE_EXE_PATH   = r"/mnt/d/voicevox_engine/run.exe"   # Windows側run.exe（WSLからはpowershell.exe経由で起動）
ENGINE_HOST       = "172.30.80.1"
ENGINE_PORTS      = [50021, 50022, 50023, 50024, 50025]  # 将来拡張用（複数起動の候補）
ENGINE_BOOT_TIMEOUT_SEC = 35

ENGINE_WORKDIR    = r"/mnt/d/voicevox_engine"
ENGINE_ARGS       = ["--host", "0.0.0.0", "--port", "50021", "--cors_policy_mode", "all"]

# ============ VOICEVOX スピーカー設定 =============
SPEAKERS = {
    "四国めたん": 2,
    "ずんだもん": 3,
    "春日部つむぎ": 8,
    "雨晴はう": 10,
    "波音リツ": 9,
    "玄野武宏": 11,
    "白上虎太郎": 12,
    "青山龍星": 13,
    "冥鳴ひまり": 14,
    "九州そら": 16,
    "もち子さん": 20,
    "さとうささら": 21,
    "小夜": 22,
}

# 逆引き（ID → 日本語話者名）
SPEAKER_ID_TO_NAME = {v: k for k, v in SPEAKERS.items()}

def resolve_speaker_id(name_or_id: str | int | None, default_name: str = "ずんだもん") -> int:
    """
    name_or_id が:
      - int / 数値文字列 → そのままID
      - 日本語名（SPEAKERSのキー） → 対応ID
      - None / 不明 → default_name で解決
    """
    if name_or_id is None:
        return SPEAKERS.get(default_name, 3)
    if isinstance(name_or_id, int):
        return name_or_id
    s = str(name_or_id).strip()
    # 数値文字列？
    if s.isdigit():
        return int(s)
    # 日本語名マッチ
    if s in SPEAKERS:
        return SPEAKERS[s]
    # だめならデフォルト
    return SPEAKERS.get(default_name, 3)