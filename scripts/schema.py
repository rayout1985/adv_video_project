# -*- coding: utf-8 -*-
"""Data structures for script schema (extensible actions)."""
from typing import List, Optional, Dict, Any, Union, TypedDict

class Action(TypedDict, total=False):
    type: str                # e.g. "fadein", "fadeout", "jump", "zoomin", "zoomout", "move"
    start: float             # seconds from the start of the line
    duration: float          # how long the action lasts
    strength: float          # generic param (e.g., scale delta, jump height)
    x: float                 # for move: delta or absolute depending on mode
    y: float
    mode: str                # e.g. "relative" or "absolute"
    easing: str              # "linear", "ease_in_out"

class Line(TypedDict, total=False):
    character: str           # path to character image
    position: str            # "left" or "right"
    voice: int               # VOICEVOX speaker id
    text: str
    action: Union[str, Action, List[Action]]  # string or array of action dicts
    bg: str                  # allow bg override per line

class Scene(TypedDict, total=False):
    bg: str                  # default background for the scene
    lines: List[Line]

Script = List[Scene]
