"""通用工具。"""
from __future__ import annotations

import time
import uuid


def now_ms() -> int:
    """epoch 毫秒。"""
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


def ms_to_mmss(ms: int | float) -> str:
    """毫秒 → MM:SS（详情页/播放器显示）。"""
    total = int(ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def ms_to_srt(ms: int | float) -> str:
    """毫秒 → SRT 时间戳 HH:MM:SS,mmm。"""
    ms = int(ms)
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    mm = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{mm:03d}"


def ms_to_vtt(ms: int | float) -> str:
    """毫秒 → VTT 时间戳 HH:MM:SS.mmm。"""
    return ms_to_srt(ms).replace(",", ".")
