# -*- coding: utf-8 -*-
from typing import List, Tuple

def format_ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def write_srt(items: List[Tuple[int, float, float, str]], out_path: str):
    """items: list of (index, start_sec, end_sec, text)"""
    with open(out_path, 'w', encoding='utf-8') as f:
        for idx, start, end, text in items:
            f.write(f"{idx}\n")
            f.write(f"{format_ts(start)} --> {format_ts(end)}\n")
            f.write(text.strip() + "\n\n")
