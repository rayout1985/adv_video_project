# -*- coding: utf-8 -*-
"""
timeline_to_projects.py  (OpenShot/adv_maker 兼用  完全版)
 - DSL(時間指定台本) → (A) adv_maker用 script.json
                    → (B) OpenShot .osp（3.3.0 / libopenshot 0.4.0 互換）
                    → (C) SRT（subtitles.srt）
 - 字幕は「プレートPNG（幅最大）」+「テキストのみPNG」を自動生成し
   L6=plate, L7=text で最前面に配置
 - キャラ配置は同時表示人数で duo(左右) / solo(中央) を自動切替
 - Windows で .osp を開くために --path-mode=winabs / --win-root をサポート
"""

from __future__ import annotations
import argparse
import json
import os
import re
import uuid
import random
import string
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath, PurePosixPath
from typing import List, Optional, Dict, Any, Tuple

# ========= レイアウト・プリセット =========
# 小さめスケールにして BG が見えるように（OpenShotの座標は -1..1 / 画面中央が 0,0）
ADV_LAYOUT = {
    "duo":  {"left_x": -0.33, "right_x":  0.33, "y": -0.12, "scale": 0.58},  # 左右 / 低め / 小さめ
    "solo": {"center_x":  0.00,           "y": -0.10, "scale": 0.78},        # 中央 / 低め / 中
}
def hold_backgrounds(evs):
    if not evs:
        return evs
    last_end = max(e.end for e in evs)
    bgs = [e for e in evs if e.kind == "bg"]
    bgs.sort(key=lambda x: x.start)
    for i, bg in enumerate(bgs):
        bg_end = bgs[i+1].start if i + 1 < len(bgs) else last_end
        # 元の end より短い場合は延長（シーン切替は次のBGで上書き）
        if bg_end > bg.end:
            bg.end = bg_end
    return evs
# 追加済みでなければファイル上部ユーティリティに置く
def build_alpha_kf_abs_secs(start_sec: float, end_sec: float, actions: list, fade_sec: float = 0.6):
    eps = 1e-6
    start = float(start_sec); end = float(end_sec)
    dur = max(end - start, eps)
    has_in  = "fadein"  in actions
    has_out = "fadeout" in actions
    f = min(max(fade_sec, 0.05), dur * 0.49)
    pts = []
    if has_in:  pts += [(start, 0.0), (start + f, 1.0)]
    else:       pts += [(start, 1.0)]
    if has_out: pts += [(end - f, 1.0), (end, 0.0)]
    else:       pts += [(end, 1.0)]
    pts.sort(key=lambda p: p[0])
    dedup=[]
    for t,y in pts:
        if dedup and abs(dedup[-1][0]-t)<eps: dedup[-1]=(t,y)
        else: dedup.append((t,y))
    return {"Points":[
        {"co":{"X":float(t),"Y":float(y)},
         "handle_left":{"X":0.5,"Y":1.0},
         "handle_right":{"X":0.5,"Y":0.0},
         "handle_type":0,"interpolation":1} for t,y in dedup]}


# ---- 追加：絶対秒キーフレーム ----
def kf_abs_points(t_y_list, interp: int = 1):
    """[(t,y), ...] を OpenShot の Points に変換（t は絶対秒）"""
    def _pt_abs(t, y, interp):
        return {
            "co": {"X": float(t), "Y": float(y)},
            "handle_left":  {"X": 0.5, "Y": 1.0},
            "handle_right": {"X": 0.5, "Y": 0.0},
            "handle_type": 0,
            "interpolation": int(interp),
        }
    return {"Points": [_pt_abs(t, y, interp) for (t, y) in t_y_list]}
def build_alpha_kf_abs(start_sec: float, end_sec: float, actions: list, ease_ratio: float = 0.25):
    """
    fadein/fadeout を両立。X は絶対秒。
    何も指定が無い場合は、区間全体 1.0（不透明）。
    """
    dur = max(0.001, end_sec - start_sec)
    e = min(max(ease_ratio, 0.05), 0.49)
    dt = dur * e

    has_in  = "fadein"  in actions
    has_out = "fadeout" in actions

    pts = []
    if has_in:
        pts += [(start_sec, 0.0), (start_sec + dt, 1.0)]
    else:
        pts += [(start_sec, 1.0)]

    if has_out:
        pts += [(end_sec - dt, 1.0), (end_sec, 0.0)]
    else:
        pts += [(end_sec, 1.0)]

    # tでソート＆重複tは後勝ち
    pts.sort(key=lambda x: x[0])
    dedup = []
    for t, y in pts:
        if dedup and abs(dedup[-1][0] - t) < 1e-6:
            dedup[-1] = (t, y)
        else:
            dedup.append((t, y))
    return kf_abs_points(dedup, interp=1)

def build_alpha_kf(actions: List[str], ease: float = 0.25) -> Dict[str, Any]:
    """fadein / fadeout を両立させた alpha カーブを生成（0..1 の相対時間）"""
    e = min(max(ease, 0.05), 0.49)  # かけ幅の安全域
    has_in  = "fadein"  in actions
    has_out = "fadeout" in actions

    pts: List[Tuple[float, float]] = []
    if has_in:
        pts += [(0.0, 0.0), (e, 1.0)]
    else:
        pts += [(0.0, 1.0)]

    if has_out:
        pts += [(1.0 - e, 1.0), (1.0, 0.0)]
    else:
        pts += [(1.0, 1.0)]

    # t を昇順で整列＆同一 t の重複を除去
    pts.sort(key=lambda p: p[0])
    dedup: List[Tuple[float, float]] = []
    for t, y in pts:
        if dedup and abs(dedup[-1][0] - t) < 1e-6:
            dedup[-1] = (t, y)  # 後勝ちで上書き
        else:
            dedup.append((t, y))
    return kf_points(dedup, interp=1)
# ========= 小ユーティリティ =========
def norm_slash(p: str) -> str:
    return p.replace("\\", "/").strip()

def tc_to_sec(s: str) -> float:
    m = re.fullmatch(r"(\d{2}):(\d{2})", s.strip())
    if not m:
        raise ValueError(f"Invalid timecode: {s}")
    return int(m.group(1)) * 60 + int(m.group(2))

def sec_to_srt(t: float) -> str:
    t = max(0.0, float(t))
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def ensure_dir_for(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

def kind_from_name(name: str) -> str:
    n = os.path.basename(norm_slash(name)).lower()
    return "bg" if n.startswith("bg") else "char"

def adv_asset_path(name: str, assets_layout: str = "categorized") -> str:
    base = os.path.basename(norm_slash(name))
    if assets_layout == "flat":
        return f"assets/{base}"
    sub = "bg" if kind_from_name(base) == "bg" else "chars"
    return f"assets/{sub}/{base}"

def make_id(n: int = 10) -> str:
    import secrets, string
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))

def try_image_size(abs_path: Path) -> Optional[tuple]:
    try:
        from PIL import Image
        with Image.open(abs_path) as im:
            return im.width, im.height
    except Exception:
        return None

def to_osp_path(file_name: str, project_dir: str, osp_out: str,
                path_mode: str, win_root: str, assets_layout: str) -> str:
    """assets ファイル（bg/chars）用: .osp に書くパスを返す"""
    adv_rel = adv_asset_path(file_name, assets_layout=assets_layout)
    if path_mode == "winabs":
        if not win_root:
            raise ValueError("--win-root が必要です（--path-mode=winabs の場合）")
        return str(PureWindowsPath(win_root) / PurePosixPath(adv_rel))
    abs_asset = (Path(project_dir).resolve() / adv_rel).resolve()
    rel = os.path.relpath(abs_asset, start=Path(osp_out).parent.resolve())
    return rel.replace("\\", "/")

def to_osp_from_project_rel(rel_path: str, project_dir: str, osp_out: str,
                            path_mode: str, win_root: str) -> str:
    """プロジェクト直下からの相対パス → .osp に書くパス（winabs/relative）"""
    if path_mode == "winabs":
        if not win_root:
            raise ValueError("--win-root が必要です（--path-mode=winabs の場合）")
        return str(PureWindowsPath(win_root) / PurePosixPath(rel_path))
    abs_path = (Path(project_dir).resolve() / rel_path).resolve()
    rel = os.path.relpath(abs_path, start=Path(osp_out).parent.resolve())
    return rel.replace("\\", "/")

# ========= DSL =========
@dataclass
class Event:
    kind: str           # "bg" | "char"
    file: str           # ファイル名
    start: float
    end: float
    actions: List[str]
    position: Optional[str] = None  # left / right
    text: Optional[str] = None      # talk の本文

def parse_dsl(path: str) -> List[Event]:
    evs: List[Event] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\s+(\S+)\s*(.*)$", line)
        if not m:
            raise ValueError(f"DSL parse error: {line}")
        t1, t2, fname, tail = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        start, end = tc_to_sec(t1), tc_to_sec(t2)
        text = None
        if "talk" in tail:
            m2 = re.search(r'talk\s+"([^"]*)"', tail)
            if m2:
                text = m2.group(1)
                tail = re.sub(r'talk\s+"[^"]*"', "talk", tail)
        tokens = [tok.lower() for tok in tail.split()] if tail else []
        pos = None
        if ("left-in" in tokens) or ("left" in tokens):
            pos = "left"
        if ("right-in" in tokens) or ("right" in tokens):
            pos = "right"
        evs.append(Event(
            kind=kind_from_name(fname),
            file=os.path.basename(norm_slash(fname)),
            start=start, end=end,
            actions=tokens, position=pos, text=text
        ))
    evs.sort(key=lambda e: (e.start, 0 if e.kind == "bg" else 1))
    return evs

# ========= レイアウト補助 =========
def layout_hint_for_chars(char_events: List[Event]) -> Dict[int, str]:
    """各キャライベントに 'solo' / 'duo' を割当て（重なり割合で判定）"""
    def overlap(a: Event, b: Event) -> float:
        return max(0.0, min(a.end, b.end) - max(a.start, b.start))
    hints: Dict[int, str] = {}
    for e in char_events:
        total = max(0.001, e.end - e.start)
        ov = sum(overlap(e, o) for o in char_events if o is not e)
        ratio = ov / total
        hints[id(e)] = "duo" if ratio > 0.35 else "solo"
    return hints

# ========= (A) adv_maker 用 JSON =========
def events_to_adv(evs: List[Event], assets_layout: str) -> List[Dict[str, Any]]:
    scenes: List[Dict[str, Any]] = []
    bgs = [e for e in evs if e.kind == "bg"]
    chars = [e for e in evs if e.kind == "char"]
    for bg in bgs:
        scene = {"bg": adv_asset_path(bg.file, assets_layout), "lines": []}
        for ch in chars:
            mid = (ch.start + ch.end) / 2.0
            if bg.start - 1e-3 <= mid <= bg.end + 1e-3:
                dur = max(0.0, ch.end - ch.start)
                acts = []
                for a in ch.actions:
                    if a in ("left-in", "right-in"):
                        acts.append({"type": "slidein", "start": 0.0, "duration": min(0.6, dur), "strength": 1.0})
                    elif a in ("fadein", "fadeout", "zoomin", "zoomout", "jump"):
                        acts.append({"type": a.replace("-", ""), "start": 0.0, "duration": min(0.6, dur), "strength": 1.0})
                line = {
                    "character": adv_asset_path(ch.file, assets_layout),
                    "position": ch.position or ("left" if ("left" in ch.actions or "left-in" in ch.actions) else "right"),
                    "voice": 3,
                    "action": acts
                }
                if ch.text:
                    line["text"] = ch.text
                scene["lines"].append(line)
        scenes.append(scene)
    return scenes

# ========= (B) OpenShot .osp 基本 =========
def base_osp_header(width:int, height:int, fps_num:int, fps_den:int) -> Dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:10].upper(),
        "fps": {"num": fps_num, "den": fps_den},
        "display_ratio": {"num": 16, "den": 9},
        "pixel_ratio": {"num": 1, "den": 1},
        "width": width, "height": height,
        "channels": 2, "channel_layout": 3, "sample_rate": 48000,
        "settings": {}, "effects": [], "markers": [], "progress": [],
        "duration": 300.0, "scale": 15.0,
        "profile": f"HD {height}p {fps_num} fps",
        "export_settings": None,
        "layers": [
            {"id":"L1","label":"","lock":False,"number":1000000,"y":0},
            {"id":"L2","label":"","lock":False,"number":2000000,"y":0},
            {"id":"L3","label":"","lock":False,"number":3000000,"y":0},  # BG
            {"id":"L4","label":"","lock":False,"number":4000000,"y":0},  # 右キャラ
            {"id":"L5","label":"","lock":False,"number":5000000,"y":0},  # 左キャラ
            {"id":"L6","label":"plate","lock":False,"number":6000000,"y":0},  # プレート
            {"id":"L7","label":"text","lock":False,"number":7000000,"y":0},   # テキスト
        ],
        "history": {"undo": [], "redo": []},
        "version": {"openshot-qt":"3.3.0","libopenshot":"0.4.0"},
        "files": [],
        "clips": []
    }

def build_file_entry(path_str: str, fps_num: int, fps_den: int,
                     proj_w: int, proj_h: int, abs_for_probe: Optional[Path]) -> Dict[str, Any]:
    ext = os.path.splitext(path_str.lower())[1]
    is_img = ext in (".png",".jpg",".jpeg",".bmp",".webp",".svg")
    fid = make_id()
    fsize = "0"
    width, height = proj_w, proj_h
    try:
        if abs_for_probe and abs_for_probe.exists():
            fsize = str(abs_for_probe.stat().st_size)
            if is_img:
                wh = try_image_size(abs_for_probe)
                if wh: width, height = wh
    except Exception:
        pass
    duration = 3600.0
    video_len = str(int((fps_num / max(fps_den,1)) * duration))
    return {
        "acodec": "",
        "audio_bit_rate": 0,
        "audio_stream_index": -1,
        "audio_timebase": {"den": 1, "num": 1},
        "channel_layout": 4,
        "channels": 0,
        "display_ratio": {"den": 9, "num": 16},
        "duration": duration,
        "file_size": fsize,
        "fps": {"den": fps_den, "num": fps_num},
        "has_audio": False,
        "has_single_image": True,
        "has_video": True,
        "height": height,
        "interlaced_frame": False,
        "metadata": {},
        "path": path_str,
        "pixel_format": 0,             # 追加: 0 (=auto)
        "pixel_ratio": {"den": 1, "num": 1},
        "sample_rate": 0,
        "top_field_first": False,      # ← True から False に
        "type": "FFmpegReader",        # ← QtImageReader から変更
        "vcodec": "",
        "video_bit_rate": 0,
        "video_length": video_len,
        "video_stream_index": -1,
        "video_timebase": {"den": fps_num, "num": 1},
        "width": width,
        "media_type": "image",
        "id": fid,
        "image": f"thumbnail/{fid}.png"
    }
def build_alpha_kf_rel_by_secs(dur_sec: float, actions: list, fade_sec: float = 0.6):
    """
    OpenShotのキーフレームX=0..1(相対)前提で、秒数固定のフェードを生成。
    fadein: 0→fade_sec で 0→1
    fadeout: (dur-fade_sec)→dur で 1→0
    どちらも無ければ全区間1.0
    """
    eps = 1e-6
    dur = max(dur_sec, eps)
    e = min(max(fade_sec / dur, 0.05), 0.49)   # 相対(0..1)に変換しつつ安全域

    has_in  = "fadein"  in actions
    has_out = "fadeout" in actions

    pts = []
    if has_in:
        pts += [(0.0, 0.0), (e, 1.0)]
    else:
        pts += [(0.0, 1.0)]
    if has_out:
        pts += [(1.0 - e, 1.0), (1.0, 0.0)]
    else:
        pts += [(1.0, 1.0)]

    # 昇順＆重複Xは後勝ちで整理
    pts.sort(key=lambda p: p[0])
    dedup = []
    for t, y in pts:
        if dedup and abs(dedup[-1][0] - t) < eps:
            dedup[-1] = (t, y)
        else:
            dedup.append((t, y))
    return {"Points": [
        {"co": {"X": float(t), "Y": float(y)},
         "handle_left": {"X": 0.5, "Y": 1.0},
         "handle_right": {"X": 0.5, "Y": 0.0},
         "handle_type": 0,
         "interpolation": 1}
        for (t, y) in dedup
    ]}
# ---- Keyframe ユーティリティ（補間=線形）----
def _pt(x: float, y: float, interp: int = 1) -> Dict[str, Any]:
    return {
        "co": {"X": float(x), "Y": float(y)},
        "handle_left":  {"X": 0.5, "Y": 1.0},
        "handle_right": {"X": 0.5, "Y": 0.0},
        "handle_type": 0,
        "interpolation": int(interp),  # 0:定数 / 1:線形
    }

def kf_const(y: float) -> Dict[str, Any]:
    return {"Points": [_pt(1.0, float(y), interp=0)]}  # 定数

def kf_pair(x0: float, y0: float, x1: float, y1: float, interp: int = 1) -> Dict[str, Any]:
    return {"Points": [_pt(x0, y0, interp), _pt(x1, y1, interp)]}

def kf_points(xys: List[Tuple[float, float]], interp: int = 1) -> Dict[str, Any]:
    return {"Points": [_pt(x, y, interp) for (x, y) in xys]}

def layer_number(kind: str, position: Optional[str]) -> int:
    if kind == "bg":
        return 3000000
    if position == "left":
        return 5000000
    return 4000000

def build_clip_entry(
    file_entry: Dict[str,Any], start_sec: float, end_sec: float,
    kind: str, position: Optional[str], actions: List[str],
    layout_mode: Optional[str] = None
) -> Dict[str, Any]:
    dur = max(0.001, end_sec - start_sec)
    ease = 0.25  # 0〜1 のクリップ相対時間（アニメのかけ幅）

    # 終点の座標とスケール
    end_x = 0.0; end_y = 0.0; base_scale = 1.0
    if kind == "char":
        mode = layout_mode or "duo"
        if mode == "duo":
            end_x = ADV_LAYOUT["duo"]["left_x"] if (position or "").startswith("left") else ADV_LAYOUT["duo"]["right_x"]
            end_y = ADV_LAYOUT["duo"]["y"]; base_scale = ADV_LAYOUT["duo"]["scale"]
        else:
            end_x = ADV_LAYOUT["solo"]["center_x"]; end_y = ADV_LAYOUT["solo"]["y"]
            base_scale = ADV_LAYOUT["solo"]["scale"]

    # 透明度
    alpha = build_alpha_kf_abs_secs(start_sec, end_sec, actions, fade_sec=0.6)

    # スケール
    scale_x = kf_const(base_scale); scale_y = kf_const(base_scale)
    if "zoomin" in actions:
        scale_x = kf_pair(0.0, base_scale, 1.0, base_scale * 1.12)
        scale_y = kf_pair(0.0, base_scale, 1.0, base_scale * 1.12)
    if "zoomout" in actions:
        scale_x = kf_pair(0.0, base_scale, 1.0, base_scale * 0.88)
        scale_y = kf_pair(0.0, base_scale, 1.0, base_scale * 0.88)

    # 位置（スライドイン＋ジャンプ）
    loc_x = kf_const(end_x); loc_y = kf_const(end_y)
    slid_in = False
    if "left-in" in actions or "left" in actions:
        loc_x = kf_pair(0.0, -1.0, min(ease, 0.4), end_x); slid_in = True
    if "right-in" in actions or "right" in actions:
        loc_x = kf_pair(0.0,  1.0, min(ease, 0.4), end_x); slid_in = True
    if "jump" in actions:
        t0 = min(ease, 0.35) if slid_in else 0.0
        loc_y = kf_points([
            (0.0, end_y),
            (t0, end_y),
            (min(1.0, t0 + 0.12), end_y - 0.12),
            (min(1.0, t0 + 0.24), end_y),
            (1.0, end_y),
        ], interp=1)

    return {
        "alpha": alpha,
        "anchor": 0,
        "channel_filter": kf_const(-1.0),
        "channel_mapping": kf_const(-1.0),
        "display": 0,
        "duration": 3600.0,
        "effects": [],
        "end": int(round(dur)),
        "gravity": 4,
        "has_audio": kf_const(-1.0),
        "has_video": kf_const(-1.0),
        "id": make_id(),
        "layer": layer_number(kind, position),
        "location_x": loc_x,
        "location_y": loc_y,
        "mixing": 0,
        "origin_x": kf_const(0.5),
        "origin_y": kf_const(0.5),
        "parentObjectId": "",
        "perspective_c1_x": kf_const(-1.0),
        "perspective_c1_y": kf_const(-1.0),
        "perspective_c2_x": kf_const(-1.0),
        "perspective_c2_y": kf_const(-1.0),
        "perspective_c3_x": kf_const(-1.0),
        "perspective_c3_y": kf_const(-1.0),
        "perspective_c4_x": kf_const(-1.0),
        "perspective_c4_y": kf_const(-1.0),
        "position": int(round(start_sec)),
        "reader": dict(file_entry),
        "rotation": kf_const(0.0),
        "scale": 1,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "shear_x": kf_const(0.0),
        "shear_y": kf_const(0.0),
        "start": 0,
        "time": kf_const(1.0),
        "volume": kf_const(1.0),
        "wave_color": {
            "alpha": kf_const(255.0),
            "blue":  kf_const(255.0),
            "green": kf_const(123.0),
            "red":   kf_const(0.0),
        },
        "waveform": False,
        "file_id": file_entry["id"],
        "title": os.path.basename(file_entry["path"])
    }

# ========= (C) 字幕PNG/プレート生成 =========
def render_subtitle_png(
    text: str, out_path: Path, W: int, H: int,
    font_path: Optional[str], font_size: int,
    pad_px: int, bottom_margin_px: int,
    fg_rgba=(255,255,255,255), bg_rgba=(0,0,0,170),
    radius: int = 18, shadow: bool = True,
    bg_mode: str = "rounded"  # "rounded" or "none"
):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    def to_px(x): return max(1, int(round(float(x))))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    W = to_px(W); H = to_px(H)
    img = Image.new("RGBA", (W, H), (0,0,0,0))
    draw = ImageDraw.Draw(img)

    # 日本語フォント自動検出
    def load_jp_font(size: int):
        candidates = []
        if font_path: candidates.append(font_path)
        candidates += [
            "/mnt/c/Windows/Fonts/meiryo.ttc",
            "/mnt/c/Windows/Fonts/meiryob.ttc",
            "/mnt/c/Windows/Fonts/msgothic.ttc",
            "/mnt/c/Windows/Fonts/YuGothR.ttc",
            "/mnt/c/Windows/Fonts/YuGothM.ttc",
            "/mnt/c/Windows/Fonts/YuGothB.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJKjp-Regular.otf",
        ]
        proj_fonts = [
            Path(out_path).parents[2] / "fonts" / "NotoSansCJKjp-Regular.otf",
            Path(out_path).parents[2] / "fonts" / "meiryo.ttc",
        ]
        candidates += [str(p) for p in proj_fonts]
        from PIL import ImageFont as _F
        for p in candidates:
            try:
                if p and Path(p).exists():
                    return _F.truetype(p, to_px(size))
            except Exception:
                pass
        try:
            return _F.truetype("DejaVuSans.ttf", to_px(size))
        except Exception:
            return _F.load_default()

    font = load_jp_font(font_size)

    # 折り返し
    def wrap_by_width(s: str, max_width: int) -> str:
        lines, buf = [], ""
        for ch in s:
            test = buf + ch
            w = draw.textlength(test, font=font)
            if w > max_width and buf:
                lines.append(buf); buf = ch
            else:
                buf = test
        if buf: lines.append(buf)
        return "\n".join(lines)

    max_text_w = int(W * 0.9)
    wrapped = "\n".join([wrap_by_width(line, max_text_w) for line in text.splitlines()])
    spacing = int(round(font_size*0.2))
    bbox = draw.multiline_textbbox((0,0), wrapped, font=font, align="center", spacing=spacing)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]

    box_w = int(round(tw + pad_px*2))
    box_h = int(round(th + pad_px*2))
    box_x = (W - box_w) // 2
    box_y = int(H - bottom_margin_px - box_h)
    box_x = max(0, min(W - box_w, box_x))
    box_y = max(0, min(H - box_h, box_y))

    if bg_mode != "none":
        if shadow:
            from PIL import Image as _I
            shadow_img = _I.new("RGBA", (box_w, box_h), (0,0,0,0))
            sd = ImageDraw.Draw(shadow_img)
            sd.rounded_rectangle((0,0,box_w,box_h), radius=int(round(radius)), fill=(0,0,0,200))
            shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=6))
            img.alpha_composite(shadow_img, (box_x+2, box_y+2))
        bg_img = Image.new("RGBA", (box_w, box_h), (0,0,0,0))
        bd = ImageDraw.Draw(bg_img)
        bd.rounded_rectangle((0,0,box_w,box_h), radius=int(round(radius)), fill=bg_rgba)
        img.alpha_composite(bg_img, (box_x, box_y))
        text_top = box_y + int(round(pad_px))
    else:
        text_top = int(H - bottom_margin_px - th)

    draw.multiline_text(
        (W//2, text_top),
        wrapped,
        font=font, fill=fg_rgba, anchor="ma", align="center",
        spacing=spacing, stroke_width=2, stroke_fill=(0,0,0,200)
    )
    img.save(out_path, "PNG")

def compose_plate_canvas(plate_src_abs: Path, out_path: Path, W:int, H:int, bottom_margin_px:int):
    """plate画像を画面幅にフィットさせ、下部に貼る"""
    from PIL import Image
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA", (int(W), int(H)), (0,0,0,0))
    plate = Image.open(plate_src_abs).convert("RGBA")
    scale = W / plate.width if plate.width else 1.0
    new_w = int(W); new_h = max(1, int(round(plate.height * scale)))
    plate = plate.resize((new_w, new_h), Image.LANCZOS)
    y = max(0, H - bottom_margin_px - new_h)
    canvas.alpha_composite(plate, (0, y))
    canvas.save(out_path, "PNG")

def make_subtitle_file_entry(path_str: str, fps_num:int, fps_den:int, W:int, H:int) -> Dict[str,Any]:
    duration = 3600.0
    video_len = str(int((fps_num / max(fps_den,1)) * duration))
    fid = make_id()
    return {
        "acodec": "", "audio_bit_rate": 0, "audio_stream_index": -1,
        "audio_timebase": {"den": 1, "num": 1},
        "channel_layout": 4, "channels": 0,
        "display_ratio": {"den": 9, "num": 16},
        "duration": duration, "file_size": "0",
        "fps": {"den": fps_den, "num": fps_num},
        "has_audio": False, "has_single_image": True, "has_video": True,
        "height": H, "interlaced_frame": False, "metadata": {},
        "path": path_str,
        "pixel_format": 0,             # 追加
        "pixel_ratio": {"den": 1, "num": 1}, "sample_rate": 0,
        "top_field_first": False,      # ← False に固定
        "type": "FFmpegReader",        # ← 変更
        "vcodec": "",
        "video_bit_rate": 0, "video_length": video_len, "video_stream_index": -1,
        "video_timebase": {"den": fps_num, "num": 1},
        "width": W, "media_type": "image", "id": fid, "image": f"thumbnail/{fid}.png"
    }

def build_subtitle_clip_entry(file_entry: Dict[str,Any], start_sec: float, end_sec: float, layer:int) -> Dict[str,Any]:
    dur = max(0.001, end_sec - start_sec)
    return {
        "alpha": kf_const(1.0),
        "anchor": 0,
        "channel_filter": kf_const(-1.0),
        "channel_mapping": kf_const(-1.0),
        "display": 0,
        "duration": 3600.0,
        "effects": [],
        "end": int(round(dur)),
        "gravity": 4,
        "has_audio": kf_const(-1.0),
        "has_video": kf_const(-1.0),
        "id": make_id(),
        "layer": layer,
        "location_x": kf_const(0.0),
        "location_y": kf_const(0.0),
        "mixing": 0,
        "origin_x": kf_const(0.5),
        "origin_y": kf_const(0.5),
        "parentObjectId": "",
        "perspective_c1_x": kf_const(-1.0),
        "perspective_c1_y": kf_const(-1.0),
        "perspective_c2_x": kf_const(-1.0),
        "perspective_c2_y": kf_const(-1.0),
        "perspective_c3_x": kf_const(-1.0),
        "perspective_c3_y": kf_const(-1.0),
        "perspective_c4_x": kf_const(-1.0),
        "perspective_c4_y": kf_const(-1.0),
        "position": int(round(start_sec)),
        "reader": dict(file_entry),
        "rotation": kf_const(0.0),
        "scale": 1,
        "scale_x": kf_const(1.0),
        "scale_y": kf_const(1.0),
        "shear_x": kf_const(0.0),
        "shear_y": kf_const(0.0),
        "start": 0,
        "time": kf_const(1.0),
        "volume": kf_const(1.0),
        "wave_color": {
            "alpha": kf_const(255.0),
            "blue":  kf_const(255.0),
            "green": kf_const(123.0),
            "red":   kf_const(0.0),
        },
        "waveform": False,
        "file_id": file_entry["id"],
        "title": os.path.basename(file_entry["path"])
    }

# ---- 字幕の重なりを自動解消（先行を切り詰め）----
def _resolve_sub_overlaps(talk_events: List[Event], policy: str = "clip", gap: float = 0.05) -> List[Event]:
    if policy == "stack":
        return talk_events
    res: List[Event] = []
    for e in sorted(talk_events, key=lambda x: x.start):
        e2 = replace(e)
        if res and e2.start < res[-1].end - 1e-3:
            res[-1].end = max(res[-1].start, e2.start - gap)
        res.append(e2)
    return res

# ========= 生成ロジック =========
def events_to_osp_and_srt(
    evs: List[Event], fps:int, width:int, height:int, title:str,
    project_dir:str, osp_out:str, path_mode:str, win_root:str, assets_layout:str,
    srt_out_path: str, font_path: Optional[str], font_size:int, pad_px:int, bottom_margin_px:int,
    args_plate_image: str
) -> Tuple[Dict[str,Any], int]:
    proj = base_osp_header(width, height, fps, 1)
    proj["title"] = title
    files: List[Dict[str, Any]] = proj["files"]
    clips: List[Dict[str, Any]] = []
    path_to_file: Dict[str, Dict[str,Any]] = {}

    # レイアウトヒント（キャラ）
    chars_only = [e for e in evs if e.kind == "char"]
    layout_hints = layout_hint_for_chars(chars_only)

    # (1) BG/CHAR クリップ
    for e in evs:
        if e.kind not in ("bg","char"):
            continue
        p_for_osp = to_osp_path(e.file, project_dir, osp_out, path_mode, win_root, assets_layout)
        adv_rel = adv_asset_path(e.file, assets_layout)
        abs_probe = (Path(project_dir).resolve() / adv_rel).resolve()
        if p_for_osp not in path_to_file:
            file_entry = build_file_entry(p_for_osp, fps, 1, width, height, abs_probe)
            files.append(file_entry)
            path_to_file[p_for_osp] = file_entry
        fobj = path_to_file[p_for_osp]
        layout_mode = layout_hints.get(id(e), "solo") if e.kind == "char" else None
        clips.append(build_clip_entry(fobj, e.start, e.end, e.kind, e.position, e.actions, layout_mode=layout_mode))

    # (2) SRT & プレート+テキストPNG
    talks = [e for e in evs if e.kind=="char" and e.text]
    talks = _resolve_sub_overlaps(talks, policy="clip")  # 重なり解消

    if talks:
        ensure_dir_for(srt_out_path)
        with open(srt_out_path, "w", encoding="utf-8") as fw:
            for i, t in enumerate(talks, 1):
                fw.write(f"{i}\n{sec_to_srt(t.start)} --> {sec_to_srt(t.end)}\n{t.text}\n\n")

        sub_dir = Path(project_dir) / "openshot" / "subtitles"
        sub_dir.mkdir(parents=True, exist_ok=True)

        # plate 入力
        plate_in = args_plate_image
        if plate_in and not Path(plate_in).is_absolute():
            plate_in = str((Path(project_dir) / plate_in).resolve())
        plate_abs = Path(plate_in) if plate_in else None

        for i, t in enumerate(talks, 1):
            # plate
            if plate_abs and plate_abs.exists():
                plate_rel = f"openshot/subtitles/plate_{i:04d}.png"
                plate_abs_out = (Path(project_dir) / plate_rel).resolve()
                compose_plate_canvas(plate_abs, plate_abs_out, width, height, bottom_margin_px)
                plate_for_osp = to_osp_from_project_rel(plate_rel, project_dir, osp_out, path_mode, win_root)
                plate_file = make_subtitle_file_entry(plate_for_osp, fps, 1, width, height)
                files.append(plate_file)
                clips.append(build_subtitle_clip_entry(plate_file, t.start, t.end, layer=6000000))

            # text（背景なし）
            text_rel  = f"openshot/subtitles/text_{i:04d}.png"
            text_abs_out  = (Path(project_dir) / text_rel).resolve()
            render_subtitle_png(
                t.text, text_abs_out, width, height,
                font_path=font_path, font_size=font_size,
                pad_px=pad_px, bottom_margin_px=bottom_margin_px,
                bg_mode="none"
            )
            text_for_osp = to_osp_from_project_rel(text_rel, project_dir, osp_out, path_mode, win_root)
            text_file = make_subtitle_file_entry(text_for_osp, fps, 1, width, height)
            files.append(text_file)
            clips.append(build_subtitle_clip_entry(text_file, t.start, t.end, layer=7000000))

    proj["clips"] = clips
    proj["duration"] = max((c["position"] + c["end"]) for c in clips) if clips else 1.0
    return proj, len(talks)

# ========= main =========
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsl", required=True, help="時間指定台本（テキスト）")
    ap.add_argument("--project", required=True, help="プロジェクトルート（assets 等のある場所）")
    ap.add_argument("--adv-out", required=True, help="出力: adv_maker 用 JSON")
    ap.add_argument("--osp-out", required=True, help="出力: OpenShot .osp")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--path-mode", choices=["relative","winabs"], default="relative",
                    help="OpenShot 用パス: relative=相対 / winabs=Windows絶対")
    ap.add_argument("--win-root", default="",
                    help="--path-mode=winabs の場合の Windows プロジェクト直下 (例: D:\\src\\adv_video_project\\projects\\Test)")
    ap.add_argument("--assets-layout", choices=["flat","categorized"], default="categorized",
                    help="assets 配下の構造: categorized=bg/chars サブフォルダ / flat=直下")
    # 字幕・プレート
    ap.add_argument("--srt-out", default=None, help="SRT 出力先（未指定なら <project>/openshot/subtitles/subtitles.srt）")
    ap.add_argument("--subtitle-font", default="", help="日本語フォント（WSLは /mnt/c/... を推奨）")
    ap.add_argument("--subtitle-font-size", type=int, default=48)
    ap.add_argument("--subtitle-padding", type=int, default=28)
    ap.add_argument("--subtitle-bottom", type=int, default=90)
    ap.add_argument("--plate-image", default="assets/ui/plate.png",
                    help="テキスト下に敷くプレートPNG（プロジェクト相対 or 絶対）")
    args = ap.parse_args()

    events = parse_dsl(args.dsl)
    events = hold_backgrounds(events) 

    # (A) ADV JSON
    adv_json = events_to_adv(events, assets_layout=args.assets_layout)
    ensure_dir_for(args.adv_out)
    Path(args.adv_out).write_text(json.dumps(adv_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # (B)(C) OSP + SRT
    srt_out = args.srt_out or str(Path(args.project) / "openshot" / "subtitles" / "subtitles.srt")
    osp_json, n_talks = events_to_osp_and_srt(
        events, args.fps, args.width, args.height,
        title=Path(args.dsl).resolve().parent.name or "ADVProject",
        project_dir=args.project, osp_out=args.osp_out,
        path_mode=args.path_mode, win_root=args.win_root, assets_layout=args.assets_layout,
        srt_out_path=srt_out,
        font_path=(args.subtitle_font or None),
        font_size=args.subtitle_font_size,
        pad_px=args.subtitle_padding,
        bottom_margin_px=args.subtitle_bottom,
        args_plate_image=args.plate_image
    )
    ensure_dir_for(args.osp_out)
    Path(args.osp_out).write_text(json.dumps(osp_json, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ ADV JSON : {args.adv_out}")
    print(f"✅ OSP      : {args.osp_out}")
    print(f"✅ SRT      : {srt_out}  (lines={n_talks})")

if __name__ == "__main__":
    main()
