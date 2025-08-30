# -*- coding: utf-8 -*-
"""
actions.py
- キャラクリップに対して時間依存のアクション（フェード/ジャンプ/ズームなど）を適用
- MoviePy 1.x / 2.x 両対応
- apply_actions(...) は adv_maker.py から呼ばれる前提のシグネチャ
"""

from typing import Callable, Dict, List, Tuple, Optional
import math
import numpy as np

# MoviePy 両対応 import
try:
    # MoviePy < 2.0
    from moviepy.editor import (
        VideoClip, ImageClip, CompositeVideoClip, AudioFileClip,
        concatenate_audioclips, ColorClip, vfx
    )
except Exception:
    # MoviePy >= 2.0
    from moviepy import (
        VideoClip, ImageClip, CompositeVideoClip, AudioFileClip,
        concatenate_audioclips, ColorClip, vfx
    )


# ====== 基本ユーティリティ ======
def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))

def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

# easing 関数
def ease_linear(u: float) -> float:
    return u

def ease_in(u: float) -> float:
    return u * u

def ease_out(u: float) -> float:
    return 1.0 - (1.0 - u) * (1.0 - u)

def ease_in_out(u: float) -> float:
    # Smoothstep
    return u * u * (3.0 - 2.0 * u)

def get_ease(name: Optional[str]) -> Callable[[float], float]:
    if not name:
        return ease_linear
    name = name.lower()
    if name in ("ease_in_out", "ease-in-out", "inout", "smooth"):
        return ease_in_out
    if name in ("ease_in", "ease-in", "in"):
        return ease_in
    if name in ("ease_out", "ease-out", "out"):
        return ease_out
    return ease_linear

def ramp_0_1(t: float, start: float, duration: float, easing: Callable[[float], float]) -> float:
    """
    時刻 t（クリップ内相対時間）に対する、区間 [start, start+duration] の 0→1 ランプ。
    """
    if duration <= 0:
        return 1.0 if t >= start else 0.0
    if t <= start:
        return 0.0
    if t >= start + duration:
        return 1.0
    u = (t - start) / duration  # 0..1
    return clamp(easing(u), 0.0, 1.0)


# ====== 不透明度マスク適用（時間依存） ======
def apply_opacity_curve(clip: ImageClip, curve: Callable[[float], float], fps: int = 30) -> ImageClip:
    """
    curve(t) -> 0..1 を返す関数に基づいて、一様マスクの VideoClip を生成して適用。
    """
    w, h = clip.w, clip.h

    def make_frame(t: float):
        alpha = float(clamp(curve(t), 0.0, 1.0))
        # 2D のマスク（h, w）
        return np.full((h, w), alpha, dtype=float)

    mask = VideoClip(make_frame=make_frame, ismask=True).set_duration(clip.duration)
    if fps:
        mask = mask.set_fps(fps)
    return clip.set_mask(mask)


# ====== アクション適用 ======
def apply_actions(
    ch_clip: ImageClip,
    line_start: float,
    line_duration: float,
    action_spec: Optional[List[Dict]],
    base_pos_xy: Tuple[int, int],
    base_scale: float,
    FPS: int = 30,
) -> ImageClip:
    """
    ch_clip:      キャラの ImageClip（この関数内で position/resize などを設定する前提）
    line_start:   このセリフの開始時刻（秒） ※clip の start とは関係なく「参照用」
    line_duration:このセリフの長さ（秒）
    action_spec:  dict or list[dict]。各要素: {"type": "...", "start": 0.2, "duration": 0.6, "strength": 1.0, "easing": "ease_in_out"}
                  "start" と "duration" はセリフ開始からの相対秒。
    base_pos_xy:  (x_px, y_px) 画面ピクセル座標（中心基準で使うならレイアウト側で調整）
    base_scale:   初期拡大率（基準倍率）
    FPS:          フレームレート（マスク生成などに使用）
    """

    # --- 標準のサイズ・位置をまず設定 ---
    # 注意: 既に外で resize/position 済みなら、ここで再設定するのは二重になるが、
    #       現在の呼び出し元シグネチャ的に apply_actions 側で責務を持つ実装にしておく。
    ch_clip = ch_clip.resize(base_scale)

    x0, y0 = base_pos_xy

    # 後で時間依存で上書きするため、いったん固定を設定しておく
    ch_clip = ch_clip.set_position((x0, y0))

    if not action_spec:
        return ch_clip

    # 単一 dict でも list に正規化
    actions = action_spec if isinstance(action_spec, list) else [action_spec]

    # 合成用の関数リスト
    alpha_funcs: List[Callable[[float], float]] = []      # 0..1 を返す
    scale_funcs: List[Callable[[float], float]] = []      # >0 の倍率を返す
    offset_x_funcs: List[Callable[[float], float]] = []   # px
    offset_y_funcs: List[Callable[[float], float]] = []   # px

    for act in actions:
        a_type = (act.get("type") or "").lower()
        a_start = float(act.get("start", 0.0))           # セリフ先頭からの相対秒（= ch_clip 内相対時間とみなす）
        a_dur = float(act.get("duration", 0.0))
        strength = float(act.get("strength", 1.0))
        easing = get_ease(act.get("easing"))

        # --- フェード系 ---
        if a_type == "fadein":
            def make_alpha(start=a_start, dur=a_dur, ease=easing):
                def alpha_fn(t: float) -> float:
                    # t < start : 0, start..start+dur : 0->1, それ以外 : 1
                    r = ramp_0_1(t, start, dur, ease)
                    return r
                return alpha_fn
            alpha_funcs.append(make_alpha())

        elif a_type == "fadeout":
            def make_alpha(start=a_start, dur=a_dur, ease=easing):
                def alpha_fn(t: float) -> float:
                    # t < start : 1, start..start+dur : 1->0, それ以降 : 0
                    r = ramp_0_1(t, start, dur, ease)
                    return 1.0 - r
                return alpha_fn
            alpha_funcs.append(make_alpha())

        # --- ジャンプ（y方向に放物線移動） ---
        elif a_type == "jump":
            # strength=1.0 でだいたい 80px の上下、好みで調整
            amp = 80.0 * strength
            def make_jump(start=a_start, dur=max(a_dur, 1e-6), ease=easing, A=amp):
                def dy_fn(t: float) -> float:
                    # 0..1 の正規化時間
                    u = ramp_0_1(t, start, dur, ease)    # 0→1 の進捗（ease 済み）
                    # 放物線: u=0,1 で 0, 中間で -A（上に跳ねる）
                    return -A * 4.0 * u * (1.0 - u)
                return dy_fn
            offset_y_funcs.append(make_jump())

        # --- 拡大縮小 ---
        elif a_type == "zoomin":
            # strength=0.3 なら 30% アップ（基準倍率に対してさらに掛ける）
            s = max(0.0, strength)
            def make_scale(start=a_start, dur=max(a_dur, 1e-6), ease=easing, S=s):
                def sc_fn(t: float) -> float:
                    u = ramp_0_1(t, start, dur, ease)
                    return 1.0 + S * u
                return sc_fn
            scale_funcs.append(make_scale())

        elif a_type == "zoomout":
            # strength=0.3 なら 30% ダウン
            s = max(0.0, strength)
            def make_scale(start=a_start, dur=max(a_dur, 1e-6), ease=easing, S=s):
                def sc_fn(t: float) -> float:
                    u = ramp_0_1(t, start, dur, ease)
                    return max(0.1, 1.0 - S * u)
                return sc_fn
            scale_funcs.append(make_scale())

        # 将来拡張用: slide, shake などはここに追加
        # elif a_type == "slide":
        #     ...
        # elif a_type == "shake":
        #     ...

        else:
            # 未知のアクションは無視（将来の互換性のためにエラーにしない）
            continue

    # ---- 合成して clip に適用 ----

    # 1) 不透明度（必要時のみ）
    if alpha_funcs:
        def alpha_combined(t: float) -> float:
            a = 1.0
            for f in alpha_funcs:
                a *= clamp(f(t), 0.0, 1.0)
            return clamp(a, 0.0, 1.0)
        ch_clip = apply_opacity_curve(ch_clip, alpha_combined, fps=FPS)

    # 2) スケール（基準倍率はすでに ch_clip.resize(base_scale) 済み。ここでは時間依存の倍率を掛ける）
    if scale_funcs:
        def scale_combined(t: float) -> float:
            s = 1.0
            for f in scale_funcs:
                s *= max(0.1, float(f(t)))
            return s
        # factor 関数で相対倍率を適用
        ch_clip = ch_clip.resize(lambda t: scale_combined(t))

    # 3) 位置オフセット（複数アクションがあれば合算）
    if offset_x_funcs or offset_y_funcs:
        def pos_fn(t: float):
            dx = 0.0
            dy = 0.0
            for f in offset_x_funcs:
                dx += float(f(t))
            for f in offset_y_funcs:
                dy += float(f(t))
            return (x0 + dx, y0 + dy)
        ch_clip = ch_clip.set_position(pos_fn)
    else:
        # 明示的に固定位置を上書き（既定）
        ch_clip = ch_clip.set_position((x0, y0))

    return ch_clip
