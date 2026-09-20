"""流式重采样工具（soxr，高质量且低 CPU）。

将采集到的任意采样率/声道音频统一转换为 16kHz mono float32。
替代 LiveTranslate 的手写线性插值（音质更好）。
"""
from __future__ import annotations

import numpy as np
import soxr

from .base import SAMPLE_RATE


class StreamResampler:
    """单声道流式重采样器（保持跨块连续性）。"""

    def __init__(self, in_rate: int, out_rate: int = SAMPLE_RATE, quality: str = "HQ"):
        self.in_rate = int(in_rate)
        self.out_rate = int(out_rate)
        self._stream = None
        if self.in_rate != self.out_rate:
            self._stream = soxr.ResampleStream(self.in_rate, self.out_rate, 1, dtype="float32", quality=quality)

    def process(self, samples: np.ndarray) -> np.ndarray:
        if samples.size == 0:
            return np.empty(0, dtype=np.float32)
        x = np.asarray(samples, dtype=np.float32)
        if self._stream is None:
            return x
        return self._stream.resample_chunk(x, last=False).astype(np.float32)

    def flush(self) -> np.ndarray:
        if self._stream is None:
            return np.empty(0, dtype=np.float32)
        return self._stream.resample_chunk(np.zeros(0, dtype=np.float32), last=True).astype(np.float32)


def to_mono(samples: np.ndarray, channels: int) -> np.ndarray:
    """多声道 → mono（按声道求均值）。"""
    if channels <= 1:
        return np.asarray(samples, dtype=np.float32).reshape(-1)
    x = np.asarray(samples, dtype=np.float32)
    if x.ndim == 1:
        # 交错格式重排为 (frames, channels)
        if x.size % channels != 0:
            x = x[: x.size - (x.size % channels)]
        x = x.reshape(-1, channels)
    return x.mean(axis=1).astype(np.float32)
