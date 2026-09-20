"""实时事件协议（对应 docs/architecture/03-realtime-protocol.md）。

前后端统一信封：{type, seq, session_id, ts, payload}。
事件类型常量集中定义，前端 types/events.ts 与之对齐。
"""
from __future__ import annotations

from typing import Any, Optional

from ..utils import now_ms

# ---- 服务端 → 客户端 事件类型 ----
# 会话生命周期
SESSION_STARTED = "session.started"
SESSION_PAUSED = "session.paused"
SESSION_RESUMED = "session.resumed"
SESSION_PROCESSING = "session.processing"
SESSION_COMPLETED = "session.completed"
SESSION_RECOVERED = "session.recovered"
SESSION_SNAPSHOT = "session.snapshot"
# 音频
AUDIO_STATUS = "audio.status"
# 转录
TRANSCRIPT_PARTIAL = "transcript.partial"
TRANSCRIPT_FINAL = "transcript.final"
# 翻译
TRANSLATION_FINAL = "translation.final"
TRANSLATION_FAILED = "translation.failed"
# 录音
RECORDING_STATUS = "recording.status"
# 其他
SPEAKER_UPDATED = "speaker.updated"
SUMMARY_STATUS = "summary.status"
MODEL_STATUS = "model.status"
ERROR = "error"

# ---- 客户端 → 服务端 消息类型 ----
C_SESSION_START = "session.start"
C_SESSION_PAUSE = "session.pause"
C_SESSION_RESUME = "session.resume"
C_SESSION_STOP = "session.stop"
C_TRANSLATION_RETRY = "translation.retry"
C_SESSION_SUBSCRIBE = "session.subscribe"


def make_event(type_: str, *, session_id: Optional[str] = None,
               payload: Optional[dict[str, Any]] = None, seq: int = 0) -> dict[str, Any]:
    return {
        "type": type_,
        "seq": seq,
        "session_id": session_id,
        "ts": now_ms(),
        "payload": payload or {},
    }


def transcript_partial(*, session_id: str, text: str, start_ms: int,
                       speaker_id: Optional[str] = None, seq: int = 0) -> dict[str, Any]:
    return make_event(TRANSCRIPT_PARTIAL, session_id=session_id, seq=seq, payload={
        "text": text, "start_ms": start_ms, "speaker_id": speaker_id,
    })


def transcript_final(*, session_id: str, segment_id: str, segment_index: int,
                     start_ms: int, end_ms: int, text: str,
                     speaker_id: Optional[str] = None, confidence: Optional[float] = None,
                     seq: int = 0) -> dict[str, Any]:
    return make_event(TRANSCRIPT_FINAL, session_id=session_id, seq=seq, payload={
        "segment_id": segment_id, "segment_index": segment_index,
        "start_ms": start_ms, "end_ms": end_ms, "speaker_id": speaker_id,
        "text": text, "confidence": confidence, "status": "committed",
    })


def translation_final(*, session_id: str, segment_id: str, text: str,
                      provider: str, seq: int = 0) -> dict[str, Any]:
    return make_event(TRANSLATION_FINAL, session_id=session_id, seq=seq, payload={
        "segment_id": segment_id, "text": text, "provider": provider, "status": "ok",
    })


def error_event(*, scope: str, code: str, message: str, suggestion: str = "",
                retryable: bool = False, session_id: Optional[str] = None, seq: int = 0) -> dict[str, Any]:
    return make_event(ERROR, session_id=session_id, seq=seq, payload={
        "scope": scope, "code": code, "message": message,
        "suggestion": suggestion, "retryable": retryable,
    })
