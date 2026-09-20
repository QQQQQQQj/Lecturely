"""AudioSource 抽象与音频帧定义。

所有采集源统一产出 16kHz / mono / float32 的 PCM 帧（对齐 ASR 内核输入契约）。
时间基准：stream_time_ms 采用音频样本计数（非墙钟），保证暂停/变速喂入时时间戳不漂移。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Protocol

import numpy as np

SAMPLE_RATE = 16000
CHANNELS = 1


class InputMode(str, enum.Enum):
    MIC = "mic"
    SYSTEM = "system"
    MIXED = "mixed"
    FILE = "file"


@dataclass
class AudioFrame:
    """一帧音频：16kHz mono float32。"""
    samples: np.ndarray          # float32 mono，形状 (n,)
    stream_time_ms: int          # 该帧结束点相对流起点的毫秒（样本计数基准）

    @property
    def duration_ms(self) -> int:
        return int(len(self.samples) / SAMPLE_RATE * 1000)

    def to_int16_bytes(self) -> bytes:
        clipped = np.clip(self.samples, -1.0, 1.0)
        return (clipped * 32767).astype(np.int16).tobytes()


class AudioSource(Protocol):
    """采集源协议。start() 失败抛 AudioSourceError（可读原因，不静默降级）。"""

    name: str
    mode: InputMode

    async def start(self) -> None: ...

    def frames(self) -> AsyncIterator[AudioFrame]:
        """异步生成音频帧；stop() 后结束。"""
        ...

    async def stop(self) -> None:
        """幂等关闭。"""
        ...

    def level(self) -> float:
        """当前 RMS 电平（0~1，UI 指示用）。"""
        ...


@dataclass
class DeviceInfo:
    id: str
    name: str
    kind: str                    # input | output(loopback)
    is_default: bool = False
    extra: Optional[dict] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "is_default": self.is_default, "extra": self.extra or {}}
