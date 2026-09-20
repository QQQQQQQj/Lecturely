"""FFmpeg 工具：二进制定位与媒体探测。

FFmpeg 以子进程方式调用（聚合形态，LGPL 合规）。二进制优先使用 imageio-ffmpeg 自带。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)

_cached_ffmpeg: Optional[str] = None


def get_ffmpeg_path() -> str:
    """返回可用的 ffmpeg 二进制路径。"""
    global _cached_ffmpeg
    if _cached_ffmpeg:
        return _cached_ffmpeg
    # 1. imageio-ffmpeg 自带
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).is_file():
            _cached_ffmpeg = path
            return path
    except Exception:
        pass
    # 2. 系统 PATH
    path = shutil.which("ffmpeg")
    if path:
        _cached_ffmpeg = path
        return path
    raise RuntimeError("未找到 ffmpeg。请运行安装脚本（将自动部署 ffmpeg）。")


def get_ffprobe_path() -> Optional[str]:
    try:
        import imageio_ffmpeg
        ff = Path(imageio_ffmpeg.get_ffmpeg_exe())
        probe = ff.parent / ff.name.replace("ffmpeg", "ffprobe")
        if probe.is_file():
            return str(probe)
    except Exception:
        pass
    return shutil.which("ffprobe")


def probe_duration_ms(file_path: str) -> Optional[int]:
    """用 ffprobe 获取媒体时长（毫秒）；失败返回 None。"""
    probe = get_ffprobe_path()
    if not probe:
        return None
    try:
        out = subprocess.run(
            [probe, "-v", "quiet", "-print_format", "json", "-show_format", file_path],
            capture_output=True, timeout=30,
        )
        data = json.loads(out.stdout.decode("utf-8", "ignore") or "{}")
        dur = float(data.get("format", {}).get("duration", 0))
        return int(dur * 1000)
    except Exception as e:  # noqa: BLE001
        logger.warning("ffprobe 探测时长失败：%s", e)
        return None
