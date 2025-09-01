# -*- coding: utf-8 -*-
"""
timeline_to_projects.py  (OpenShot / adv_maker 兼用・完全版)

機能概要
- DSL(時間指定台本) -> (A) adv_maker 用 script.json
                   -> (B) OpenShot .osp（3.3.0 / libopenshot 0.4.0 互換）
                   -> (C) SRT (字幕)
- BGはアルファ=1で常時表示。フェード等のアクションは一時停止 (--ignore-actions)
- 立ち絵は 自動レイアウト: SOLO(中央) / DUO(左右)。レイアウト変化点で自動分割し、
  各区間ごとに “その区間だけ有効” な定数キーフレームで位置を確定（前区間のキーの影響を遮断）
- 字幕は plate.png(幅最大) + 文字PNG を自動生成（L6=plate, L7=text）
- 音声: projects/<Project>/voices/line_001.wav ... をトーク区間に自動配置(--audio-placement lines)
- Windows で .osp を開けるように --path-mode=winabs / --win-root サポート

使い方（例）
./.venv/bin/python scripts/timeline_to_projects.py \
  --dsl projects/Test/scripts/timeline.txt \
  --project projects/Test \
  --adv-out projects/Test/scripts/script.json \
  --osp-out projects/Test/openshot/project.osp \
  --fps 24 --width 1920 --height 1080 \
  --path-mode relative \
  --assets-layout categorized \
  --subtitle-font "/mnt/c/Windows/Fonts/meiryo.ttc" \
  --subtitle-font-size 60 \
  --subtitle-bottom 94 \
  --plate-image "assets/ui/plate.png" \
  --ignore-actions \
  --audio-placement lines
"""

from __future__ import annotations
import argparse, json, os, re, uuid, math, wave, contextlib
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath, PurePosixPath
from typing import List, Dict, Any, Optional, Tuple

# =========================
# 定数 / レイアウト
# =========================
ADV_LAYOUT = {
    "duo":  {"left_x": -0.33, "right_x":  0.33, "y": -0.12, "scale": 0.58},
    "solo": {"center_x": 0.00,               "y": -0.10, "scale": 0.78},
}
# レイヤー番号（大きいほど手前）
L_AUDIO  = 1000000
L_BG     = 3000000
L_CHAR_L = 4000000
L_CHAR_R = 5000000
L_PLATE  = 6000000
L_TEXT   = 7000000

# =========================
# 便利関数
# =========================
def norm(p: str) -> str:
    return p.replace("\\", "/").strip()

def tc_to_sec(mmss: str) -> float:
    m = re.fullmatch(r"(\d{2}):(\d{2})", mmss.strip())
    if not m: raise ValueError(f"Invalid timecode: {mmss}")
    return int(m.group(1))*60 + int(m.group(2))

def sec_to_srt(t: float) -> str:
    t = max(0.0, float(t))
    h = int(t // 3600); m = int((t % 3600)//60); s = int(t % 60)
    ms = int(round((t - int(t))*1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def ensure_dir_for(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

def make_id(n: int = 10) -> str:
    import secrets, string
    a = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(a) for _ in range(n))

def kind_from_name(name: str) -> str:
    n = os.path.basename(norm(name)).lower()
    return "bg" if n.startswith("bg") else "char"

def adv_asset_path(file_name: str, assets_layout: str = "categorized") -> str:
    base = os.path.basename(norm(file_name))
    if assets_layout == "flat":
        return f"assets/{base}"
    sub = "bg" if kind_from_name(base) == "bg" else "chars"
    return f"assets/{sub}/{base}"

def to_osp_path(file_name: str, project_dir: str, osp_out: str,
                path_mode: str, win_root: str, assets_layout: str) -> str:
    rel = adv_asset_path(file_name, assets_layout)
    if path_mode == "winabs":
        if not win_root:
            raise ValueError("--win-root が必要です")
        return str(PureWindowsPath(win_root) / PurePosixPath(rel))
    abs_asset = (Path(project_dir).resolve() / rel).resolve()
    return os.path.relpath(abs_asset, start=Path(osp_out).parent.resolve()).replace("\\","/")

def to_osp_from_project_rel(rel_path: str, project_dir: str, osp_out: str,
                            path_mode: str, win_root: str) -> str:
    if path_mode == "winabs":
        if not win_root: raise ValueError("--win-root が必要です")
        return str(PureWindowsPath(win_root) / PurePosixPath(rel_path))
    abs_path = (Path(project_dir).resolve() / rel_path).resolve()
    return os.path.relpath(abs_path, start=Path(osp_out).parent.resolve()).replace("\\","/")

# =========================
# DSL
# =========================
@dataclass
class Event:
    kind: str           # "bg" | "char"
    file: str
    start: float
    end: float
    actions: list[str]
    position: str | None = None   # "left" | "right"
    text: str | None = None       # talk 本文
    speaker: str | int | None = None  # ★追加: 日本語名 or 数値ID

def parse_dsl(path: str) -> list[Event]:
    evs: list[Event] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        m = re.match(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\s+(\S+)\s*(.*)$", line)
        if not m:
            raise ValueError(f"DSL parse error: {line}")
        t1, t2, fname, tail = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        start, end = tc_to_sec(t1), tc_to_sec(t2)

        # talk "..." を取り出し
        text = None
        if "talk" in tail:
            m2 = re.search(r'talk\s+"([^"]*)"', tail)
            if m2:
                text = m2.group(1)
                tail = re.sub(r'talk\s+"[^"]*"', "talk", tail)

        tokens = [tok for tok in tail.split()] if tail else []

        # spk:「日本語」 or spk:日本語 or spk:数値 を拾う
        spk = None
        new_tokens = []
        for tok in tokens:
            msp = re.match(r'spk:(?:"([^"]+)"|(\S+))', tok, flags=re.IGNORECASE)
            if msp:
                spk = msp.group(1) or msp.group(2)  # 日本語 or 数値文字列
            else:
                new_tokens.append(tok)
        tokens = [t.lower() for t in new_tokens]

        pos = None
        if ("left-in" in tokens) or ("left" in tokens):
            pos = "left"
        if ("right-in" in tokens) or ("right" in tokens):
            pos = "right"

        evs.append(Event(
            kind=kind_from_name(fname),
            file=os.path.basename(norm_slash(fname)),
            start=start, end=end,
            actions=tokens, position=pos, text=text,
            speaker=spk  # ★そのまま保持（adv_makerでID解決）
        ))
    evs.sort(key=lambda e: (e.start, 0 if e.kind == "bg" else 1))
    return evs


def hold_backgrounds(evs: List[Event]) -> List[Event]:
    if not evs: return evs
    last_end = max(e.end for e in evs)
    bgs = [e for e in evs if e.kind=="bg"]
    bgs.sort(key=lambda x: x.start)
    for i, bg in enumerate(bgs):
        bg_end = bgs[i+1].start if i+1<len(bgs) else last_end
        if bg_end > bg.end: bg.end = bg_end
    return evs

# =========================
# ADV JSON
# =========================
def events_to_adv(evs: list[Event], assets_layout: str) -> list[dict]:
    scenes: list[dict] = []
    bgs = [e for e in evs if e.kind == "bg"]
    chars = [e for e in evs if e.kind == "char"]

    for bg in bgs:
        scene = {"bg": adv_asset_path(bg.file, assets_layout), "lines": []}
        for ch in chars:
            mid = (ch.start + ch.end) / 2.0
            if bg.start - 1e-3 <= mid <= bg.end + 1e-3:
                line = {
                    "character": adv_asset_path(ch.file, assets_layout),
                    "position": ch.position or ("left" if ("left" in ch.actions or "left-in" in ch.actions) else "right"),
                }
                if ch.text:
                    line["text"] = ch.text
                # ★speaker は日本語名または数値文字列のまま出力
                if ch.speaker is not None:
                    line["speaker"] = ch.speaker
                scene["lines"].append(line)
        scenes.append(scene)
    return scenes


# =========================
# OpenShot: キーフレーム
# =========================
def _pt(x: float, y: float, interp: int = 1) -> Dict[str,Any]:
    return {
        "co": {"X": float(x), "Y": float(y)},
        "handle_left":  {"X": 0.5, "Y": 1.0},
        "handle_right": {"X": 0.5, "Y": 0.0},
        "handle_type": 0,
        "interpolation": int(interp),  # 0:const, 1:linear
    }

def kf_const(y: float) -> Dict[str,Any]:
    return {"Points": [_pt(1.0, float(y), interp=0)]}

def kf_pair(x0: float, y0: float, x1: float, y1: float, interp: int = 1) -> Dict[str,Any]:
    return {"Points": [_pt(x0, y0, interp), _pt(x1, y1, interp)]}

def kf_points(xys: List[Tuple[float,float]], interp: int = 1) -> Dict[str,Any]:
    return {"Points": [_pt(x, y, interp) for (x,y) in xys]}

# =========================
# OpenShot: ファイル/クリップ
# =========================
def base_osp_header(width:int, height:int, fps:int) -> Dict[str,Any]:
    return {
        "id": uuid.uuid4().hex[:10].upper(),
        "fps": {"num": fps, "den": 1},
        "display_ratio": {"num": 16, "den": 9},
        "pixel_ratio": {"num": 1, "den": 1},
        "width": width, "height": height,
        "channels": 2, "channel_layout": 3, "sample_rate": 48000,
        "settings": {}, "effects": [], "markers": [], "progress": [],
        "duration": 300.0, "scale": 15.0,
        "profile": f"HD {height}p {fps} fps",
        "export_settings": None,
        "layers": [
            {"id":"L1","label":"audio2","lock":False,"number":L_AUDIO,"y":0},
            {"id":"L3","label":"bg","lock":False,"number":L_BG,"y":0},
            {"id":"L4","label":"char_left","lock":False,"number":L_CHAR_L,"y":0},
            {"id":"L5","label":"char_right","lock":False,"number":L_CHAR_R,"y":0},
            {"id":"L6","label":"plate","lock":False,"number":L_PLATE,"y":0},
            {"id":"L7","label":"text","lock":False,"number":L_TEXT,"y":0},
        ],
        "history":{"undo":[],"redo":[]},
        "version":{"openshot-qt":"3.3.0","libopenshot":"0.4.0"},
        "files": [], "clips": []
    }

def try_image_size(abs_path: Path) -> Optional[Tuple[int,int]]:
    try:
        from PIL import Image
        with Image.open(abs_path) as im:
            return im.width, im.height
    except Exception:
        return None

def build_img_file_entry(path_str: str, fps:int, proj_w:int, proj_h:int,
                         abs_for_probe: Optional[Path]) -> Dict[str,Any]:
    fid = make_id()
    fsize = "0"
    width, height = proj_w, proj_h
    try:
        if abs_for_probe and abs_for_probe.exists():
            fsize = str(abs_for_probe.stat().st_size)
            wh = try_image_size(abs_for_probe)
            if wh: width, height = wh
    except Exception:
        pass
    duration = 3600.0
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
        "fps": {"den": 1, "num": fps},
        "has_audio": False,
        "has_single_image": True,
        "has_video": True,
        "height": height,
        "interlaced_frame": False,
        "metadata": {},
        "path": path_str,
        "pixel_format": 0,
        "pixel_ratio": {"den": 1, "num": 1},
        "sample_rate": 0,
        "top_field_first": False,
        "type": "FFmpegReader",  # 重要
        "vcodec": "",
        "video_bit_rate": 0,
        "video_length": str(int(fps*duration)),
        "video_stream_index": -1,
        "video_timebase": {"den": fps, "num": 1},
        "width": width,
        "media_type": "image",
        "id": fid,
        "image": f"thumbnail/{fid}.png"
    }

def probe_wav(abs_path: Path) -> Tuple[float,int,int,str]:
    # duration, sample_rate, channels, acodec
    with contextlib.closing(wave.open(str(abs_path), "rb")) as wf:
        ch = wf.getnchannels()
        sr = wf.getframerate()
        width = wf.getsampwidth()
        nframes = wf.getnframes()
        dur = nframes / float(sr) if sr else 0.0
        # 16bit=2byte を想定
        acodec = "pcm_s16le" if width==2 else "pcm_s8" if width==1 else "pcm_s32le"
        return dur, sr, ch, acodec

def build_audio_file_entry(path_str: str, abs_for_probe: Optional[Path], fps:int) -> Dict[str,Any]:
    fid = make_id()
    dur = 1.0; sr = 24000; ch = 1; acodec = "pcm_s16le"
    fsize = "0"
    try:
        if abs_for_probe and abs_for_probe.exists():
            fsize = str(abs_for_probe.stat().st_size)
            dur, sr, ch, acodec = probe_wav(abs_for_probe)
    except Exception:
        pass
    frames = int(max(1, round(dur*fps)))
    return {
        "acodec": acodec,
        "audio_bit_rate": sr*ch*16,   # おおよそ
        "audio_stream_index": 0,
        "audio_timebase": {"den": sr, "num": 1},
        "channel_layout": 4,
        "channels": ch,
        "display_ratio": {"den": 1, "num": 1},
        "duration": float(dur),
        "file_size": fsize,
        "fps": {"den": 1, "num": fps},
        "has_audio": True,
        "has_single_image": False,
        "has_video": False,
        "height": 1080,
        "interlaced_frame": False,
        "metadata": {},
        "path": path_str,
        "pixel_format": -1,
        "pixel_ratio": {"den": 1, "num": 1},
        "sample_rate": sr,
        "top_field_first": True,
        "type": "FFmpegReader",
        "vcodec": "",
        "video_bit_rate": 0,
        "video_length": str(frames),
        "video_stream_index": -1,
        "video_timebase": {"den": fps, "num": 1},
        "width": 1920,
        "media_type": "audio",
        "id": fid,
        "image": f"thumbnail/{fid}.png"
    }

def build_clip_entry(file_entry: Dict[str,Any], start_sec: float, end_sec: float,
                     *, layer:int,
                     loc_x: float = 0.0, loc_y: float = 0.0,
                     scale: float = 1.0,
                     alpha: float = 1.0) -> Dict[str,Any]:
    dur = max(0.001, end_sec-start_sec)
    return {
        "alpha": kf_const(alpha),
        "anchor": 0,
        "channel_filter": kf_const(-1.0),
        "channel_mapping": kf_const(-1.0),
        "display": 0,
        "duration": 3600.0,
        "effects": [],
        "end": int(math.ceil(dur)),
        "gravity": 4,
        "has_audio": kf_const(-1.0 if file_entry.get("media_type")!="audio" else 1.0),
        "has_video": kf_const(1.0 if file_entry.get("media_type")!="audio" else -1.0),
        "id": make_id(),
        "layer": layer,
        "location_x": kf_const(loc_x),   # <<< 区間ごとに定数化（前区間の影響を遮断）
        "location_y": kf_const(loc_y),
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
        "scale_x": kf_const(scale),
        "scale_y": kf_const(scale),
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
        "title": os.path.basename(file_entry["path"]),
    }

# =========================
# 字幕 PNG / プレート合成
# =========================
def render_subtitle_png(text: str, out_path: Path, W:int, H:int,
                        font_path: Optional[str], font_size:int,
                        pad_px:int, bottom_px:int,
                        fg=(255,255,255,255), bg=(0,0,0,170),
                        radius:int=18):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    def to_px(x): return max(1, int(round(float(x))))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    W = to_px(W); H = to_px(H)
    img = Image.new("RGBA", (W,H), (0,0,0,0))
    draw = ImageDraw.Draw(img)

    def load_font(sz:int):
        cands = []
        if font_path: cands.append(font_path)
        cands += [
            "/mnt/c/Windows/Fonts/meiryo.ttc",
            "/mnt/c/Windows/Fonts/YuGothR.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJKjp-Regular.otf",
        ]
        from PIL import ImageFont as F
        for p in cands:
            try:
                if p and Path(p).exists(): return F.truetype(p, to_px(sz))
            except Exception: pass
        try: return F.truetype("DejaVuSans.ttf", to_px(sz))
        except Exception: return F.load_default()

    font = load_font(font_size)

    def wrap_by_width(s: str, max_w: int) -> str:
        lines, buf = [], ""
        for ch in s:
            test = buf + ch
            w = draw.textlength(test, font=font)
            if w > max_w and buf:
                lines.append(buf); buf = ch
            else:
                buf = test
        if buf: lines.append(buf)
        return "\n".join(lines)

    max_text_w = int(W*0.9)
    wrapped = "\n".join([wrap_by_width(line, max_text_w) for line in text.splitlines()])
    spacing = int(round(font_size*0.2))
    bbox = draw.multiline_textbbox((0,0), wrapped, font=font, align="center", spacing=spacing)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]

    box_w = int(round(tw + pad_px*2))
    box_h = int(round(th + pad_px*2))
    box_x = (W - box_w)//2
    box_y = max(0, H - bottom_px - box_h)

    # 背景ボックス + 影
    shadow = Image.new("RGBA", (box_w, box_h), (0,0,0,0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((0,0,box_w,box_h), radius=int(round(radius)), fill=(0,0,0,200))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))
    img.alpha_composite(shadow, (box_x+2, box_y+2))

    bgimg = Image.new("RGBA",(box_w,box_h),(0,0,0,0))
    bd = ImageDraw.Draw(bgimg)
    bd.rounded_rectangle((0,0,box_w,box_h), radius=int(round(radius)), fill=bg)
    img.alpha_composite(bgimg,(box_x,box_y))

    draw.multiline_text((W//2, box_y+int(round(pad_px))),
        wrapped, font=font, fill=fg, anchor="ma", align="center",
        spacing=spacing, stroke_width=2, stroke_fill=(0,0,0,200))

    img.save(out_path, "PNG")

def compose_plate_canvas(plate_src_abs: Path, out_path: Path, W:int, H:int, bottom_px:int):
    from PIL import Image
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA",(int(W),int(H)),(0,0,0,0))
    plate = Image.open(plate_src_abs).convert("RGBA")
    scale = W / plate.width if plate.width else 1.0
    new_w = int(W); new_h = max(1, int(round(plate.height * scale)))
    plate = plate.resize((new_w,new_h), Image.LANCZOS)
    y = max(0, H-bottom_px-new_h)
    canvas.alpha_composite(plate, (0,y))
    canvas.save(out_path, "PNG")

# =========================
# 立ち絵: レイアウト分割
# =========================
@dataclass
class CharSeg:
    file: str
    start: float
    end: float
    seat: str   # 'left' | 'right' | 'center'

def split_chars_for_layout(evs: List[Event]) -> List[CharSeg]:
    chars = [e for e in evs if e.kind=="char"]
    if not chars: return []

    # 切れ目（全キャラの start/end）
    cuts = sorted(set([t for e in chars for t in (e.start, e.end)]))
    segs: List[CharSeg] = []

    # 状態：直前デュオの座席
    last_duo_map: Dict[str,str] = {}

    for i in range(len(cuts)-1):
        a,b = cuts[i], cuts[i+1]
        visible = [e for e in chars if e.start < b-1e-6 and e.end > a+1e-6]
        if not visible: continue
        if len(visible)==1:
            v = visible[0]
            segs.append(CharSeg(v.file, a, b, "center"))
            continue

        # DUO: 左右割当
        v1, v2 = sorted(visible, key=lambda e: (e.position or "", e.start, e.file))
        # ルール: 明示 > 慣性 > 名前順
        left_name  = None
        right_name = None
        if v1.position=="left":  left_name=v1.file
        if v1.position=="right": right_name=v1.file
        if v2.position=="left" and not left_name:   left_name=v2.file
        if v2.position=="right" and not right_name: right_name=v2.file
        if (not left_name or not right_name) and last_duo_map:
            for f,seat in last_duo_map.items():
                if f in (v1.file, v2.file):
                    if seat=="left" and not left_name: left_name=f
                    if seat=="right"and not right_name:right_name=f
        if not left_name:  left_name  = min(v1.file, v2.file)
        if not right_name: right_name = v2.file if left_name==v1.file else v1.file
        last_duo_map = {left_name:"left", right_name:"right"}

        for v in visible:
            seat = "left" if v.file==left_name else ("right" if v.file==right_name else "center")
            segs.append(CharSeg(v.file, a, b, seat))
    return segs

# =========================
# OSP + SRT 生成
# =========================
def events_to_osp_and_srt(
    evs: List[Event], fps:int, width:int, height:int, title:str,
    project_dir:str, osp_out:str, path_mode:str, win_root:str, assets_layout:str,
    srt_out_path:str, font_path: Optional[str], font_size:int, pad_px:int, bottom_px:int,
    plate_image: str, ignore_actions: bool, audio_placement: str
) -> Tuple[Dict[str,Any], int]:

    proj = base_osp_header(width, height, fps)
    proj["title"] = title
    files: List[Dict[str,Any]] = proj["files"]
    clips: List[Dict[str,Any]] = []

    # ------------ 画像（BG/CHAR）ファイル辞書
    img_file_cache: Dict[str,Dict[str,Any]] = {}
    def get_img_file(fname: str) -> Dict[str,Any]:
        p_for_osp = to_osp_path(fname, project_dir, osp_out, path_mode, win_root, assets_layout)
        if p_for_osp in img_file_cache: return img_file_cache[p_for_osp]
        adv_rel = adv_asset_path(fname, assets_layout)
        abs_probe = (Path(project_dir).resolve() / adv_rel).resolve()
        fe = build_img_file_entry(p_for_osp, fps, width, height, abs_probe)
        files.append(fe); img_file_cache[p_for_osp]=fe
        return fe

    # ------------ BG クリップ（常時アルファ1）
    for e in [x for x in evs if x.kind=="bg"]:
        fe = get_img_file(e.file)
        clips.append(build_clip_entry(
            fe, e.start, e.end, layer=L_BG,
            loc_x=0.0, loc_y=0.0, scale=1.0, alpha=1.0
        ))

    # ------------ 立ち絵（分割後に配置）
    char_segs = split_chars_for_layout(evs)
    # レイヤーと座標
    def seat2layer(seat:str) -> int:
        if seat=="left":  return L_CHAR_L
        if seat=="right": return L_CHAR_R
        return L_CHAR_L  # center は左レイヤーに置く（位置は中央）
    def seat2xy(seat:str) -> Tuple[float,float,float]:
        if seat=="left":
            return ADV_LAYOUT["duo"]["left_x"], ADV_LAYOUT["duo"]["y"], ADV_LAYOUT["duo"]["scale"]
        if seat=="right":
            return ADV_LAYOUT["duo"]["right_x"], ADV_LAYOUT["duo"]["y"], ADV_LAYOUT["duo"]["scale"]
        return ADV_LAYOUT["solo"]["center_x"], ADV_LAYOUT["solo"]["y"], ADV_LAYOUT["solo"]["scale"]

    for seg in char_segs:
        fe = get_img_file(seg.file)
        x,y,sc = seat2xy(seg.seat)
        clips.append(build_clip_entry(fe, seg.start, seg.end,
                                      layer=seat2layer(seg.seat),
                                      loc_x=x, loc_y=y, scale=sc, alpha=1.0))

    # ------------ SRT & plate/text PNG
    talks = [e for e in evs if e.kind=="char" and e.text]
    talks.sort(key=lambda t: (t.start, t.end))
    n_lines = len(talks)

    if n_lines:
        ensure_dir_for(srt_out_path)
        with open(srt_out_path,"w",encoding="utf-8") as fw:
            for i,t in enumerate(talks,1):
                fw.write(f"{i}\n{sec_to_srt(t.start)} --> {sec_to_srt(t.end)}\n{t.text}\n\n")

        sub_dir = Path(project_dir)/"openshot"/"subtitles"
        sub_dir.mkdir(parents=True, exist_ok=True)
        # plate 入力
        plate_abs = None
        if plate_image:
            plate_abs = Path(plate_image)
            if not plate_abs.is_absolute():
                plate_abs = (Path(project_dir)/plate_image).resolve()

        for i,t in enumerate(talks,1):
            if plate_abs and plate_abs.exists():
                plate_rel = f"openshot/subtitles/plate_{i:04d}.png"
                plate_abs_out = (Path(project_dir)/plate_rel).resolve()
                compose_plate_canvas(plate_abs, plate_abs_out, width, height, bottom_px)
                p_for_osp = to_osp_from_project_rel(plate_rel, project_dir, osp_out, path_mode, win_root)
                fe = build_img_file_entry(p_for_osp, fps, width, height, plate_abs_out)
                files.append(fe)
                clips.append(build_clip_entry(fe, t.start, t.end, layer=L_PLATE, loc_x=0.0, loc_y=0.0, scale=1.0, alpha=1.0))

            text_rel = f"openshot/subtitles/text_{i:04d}.png"
            text_abs_out = (Path(project_dir)/text_rel).resolve()
            render_subtitle_png(t.text, text_abs_out, width, height,
                                font_path, font_size, pad_px, bottom_px)
            p_for_osp = to_osp_from_project_rel(text_rel, project_dir, osp_out, path_mode, win_root)
            fe = build_img_file_entry(p_for_osp, fps, width, height, text_abs_out)
            files.append(fe)
            clips.append(build_clip_entry(fe, t.start, t.end, layer=L_TEXT, loc_x=0.0, loc_y=0.0, scale=1.0, alpha=1.0))

    # ------------ 音声配置（lines）
    if audio_placement == "lines" and n_lines:
        voices_dir = Path(project_dir)/"voices"
        wavs = []
        for i in range(1, n_lines+1):
            p = voices_dir/f"line_{i:03d}.wav"
            if p.exists(): wavs.append(p)
        for i,(t) in enumerate(talks, start=1):
            p = voices_dir/f"line_{i:03d}.wav"
            if not p.exists(): continue
            rel = os.path.relpath(p, start=Path(project_dir)).replace("\\","/")
            p_for_osp = to_osp_from_project_rel(rel, project_dir, osp_out, path_mode, win_root)
            fe = build_audio_file_entry(p_for_osp, p, fps)
            files.append(fe)
            clips.append(build_clip_entry(fe, t.start, t.end, layer=L_AUDIO,
                                          loc_x=0.0, loc_y=0.0, scale=1.0, alpha=1.0))

    proj["clips"] = clips
    proj["duration"] = max((c["position"] + c["end"]) for c in clips) if clips else 1.0
    return proj, n_lines

# =========================
# main
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsl", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--adv-out", required=True)
    ap.add_argument("--osp-out", required=True)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--path-mode", choices=["relative","winabs"], default="relative")
    ap.add_argument("--win-root", default="")
    ap.add_argument("--assets-layout", choices=["flat","categorized"], default="categorized")
    # 字幕・UI
    ap.add_argument("--srt-out", default=None)
    ap.add_argument("--subtitle-font", default="")
    ap.add_argument("--subtitle-font-size", type=int, default=60)
    ap.add_argument("--subtitle-padding", type=int, default=28)
    ap.add_argument("--subtitle-bottom", type=int, default=94)
    ap.add_argument("--plate-image", default="assets/ui/plate.png")
    # 制御
    ap.add_argument("--ignore-actions", action="store_true")  # 将来拡張用（いまは固定配置）
    ap.add_argument("--audio-placement", choices=["none","lines"], default="lines")

    args = ap.parse_args()

    events = parse_dsl(args.dsl)
    events = hold_backgrounds(events)

    # (A) adv_maker JSON
    adv_json = events_to_adv(events, assets_layout=args.assets_layout)
    ensure_dir_for(args.adv_out)
    Path(args.adv_out).write_text(json.dumps(adv_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # (B)(C) OSP + SRT
    srt_out = args.srt_out or str(Path(args.project)/"openshot"/"subtitles"/"subtitles.srt")
    osp_json, n_talks = events_to_osp_and_srt(
        events, args.fps, args.width, args.height,
        title=Path(args.dsl).stem,
        project_dir=args.project, osp_out=args.osp_out,
        path_mode=args.path_mode, win_root=args.win_root, assets_layout=args.assets_layout,
        srt_out_path=srt_out,
        font_path=(args.subtitle_font or None),
        font_size=args.subtitle_font_size,
        pad_px=args.subtitle_padding,
        bottom_px=args.subtitle_bottom,
        plate_image=args.plate_image,
        ignore_actions=args.ignore_actions,
        audio_placement=args.audio_placement
    )
    ensure_dir_for(args.osp_out)
    Path(args.osp_out).write_text(json.dumps(osp_json, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ ADV JSON : {args.adv_out}")
    print(f"✅ OSP      : {args.osp_out}")
    print(f"✅ SRT      : {srt_out}  (lines={n_talks})")

if __name__ == "__main__":
    main()
