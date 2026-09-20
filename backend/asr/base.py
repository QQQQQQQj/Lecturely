"""ASRProvider 抽象与事件定义（对应 docs/architecture/05 §3）。

业务层只面向本接口编程，不依赖具体 Whisper 实现。
事件分两类：Partial（未确认，仅 UI）与 CommittedSentence（已确认句子，可持久化）。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, Protocol

from ..audio.base import AudioFrame


class ASREventKind(str, enum.Enum):
    PARTIAL = "partial"
    COMMITTED = "committed"
    ENGINE_READY = "engine_ready"
    ENGINE_ERROR = "engine_error"


@dataclass
class PartialEvent:
    kind: ASREventKind = field(default=ASREventKind.PARTIAL, init=False)
    text: str = ""
    start_ms: int = 0


@dataclass
class CommittedSentence:
    """一条已确认的稳定句子（ADR-003：非重叠、可持久化）。"""
    text: str = ""
    start_ms: int = 0
    end_ms: int = 0
    confidence: Optional[float] = None
    speaker_id: Optional[str] = None
    detected_language: Optional[str] = None
    kind: ASREventKind = field(default=ASREventKind.COMMITTED, init=False)


@dataclass
class EngineStatusEvent:
    kind: ASREventKind
    message: str = ""


@dataclass
class SessionASRConfig:
    language: str = "auto"          # 源语言（auto/en/zh/...）
    model: str = "small"
    device: str = "auto"
    compute_type: str = "int8"


@dataclass
class ASRCapabilities:
    streaming: bool = True
    word_timestamps: bool = True
    diarization: bool = False
    languages: Optional[list[str]] = None


class ASRProvider(Protocol):
    async def start_session(self, cfg: SessionASRConfig) -> None: ...
    async def push_audio(self, frame: AudioFrame) -> None: ...
    def events(self) -> AsyncIterator[PartialEvent | CommittedSentence | EngineStatusEvent]:
        """产出识别事件；会话结束（flush 完成）后结束。"""
        ...
    async def flush(self) -> None:
        """结束时强制提交尾部未确认内容。"""
        ...
    async def close(self) -> None: ...
    def capabilities(self) -> ASRCapabilities: ...
