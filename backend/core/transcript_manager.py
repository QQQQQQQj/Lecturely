"""TranscriptManager：Segment 持久化与实时广播。

纪律（ADR-003/005）：
- Partial 仅广播，永不落库。
- Committed 句子先幂等落库（UNIQUE(session_id, segment_index)），成功后才广播 transcript.final。
- segment_index 会话内单调递增；重复提交被去重。
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from ..asr.base import CommittedSentence
from ..logging_setup import get_logger
from ..storage.repositories.sessions import SegmentRepository
from ..websocket import events as ev

logger = get_logger(__name__)

Broadcast = Callable[[dict], Awaitable[None]]


class TranscriptManager:
    def __init__(self, *, session_id: str, source_language: str, target_language: str,
                 broadcast: Broadcast):
        self._session_id = session_id
        self._source_language = source_language
        self._target_language = target_language
        self._broadcast = broadcast
        self._repo = SegmentRepository()
        self._segment_index = 0
        self._committed: list[dict] = []

    @property
    def segment_count(self) -> int:
        return self._segment_index

    @property
    def committed_segments(self) -> list[dict]:
        return list(self._committed)

    async def handle_partial(self, text: str, start_ms: int) -> None:
        if not text:
            return
        await self._broadcast(ev.transcript_partial(
            session_id=self._session_id, text=text, start_ms=start_ms,
        ))

    async def handle_committed(self, sentence: CommittedSentence) -> Optional[dict]:
        text = (sentence.text or "").strip()
        if not text:
            return None
        source_lang = sentence.detected_language or self._source_language
        if source_lang == "auto" and sentence.detected_language:
            source_lang = sentence.detected_language
        seg = await self._repo.commit(
            session_id=self._session_id,
            segment_index=self._segment_index,
            start_ms=sentence.start_ms, end_ms=sentence.end_ms,
            source_text=text,
            source_language=source_lang if source_lang != "auto" else (sentence.detected_language or "auto"),
            target_language=self._target_language,
            speaker_id=sentence.speaker_id,
            confidence=sentence.confidence,
        )
        if seg is None:
            logger.debug("重复 segment 被去重：session=%s index=%d", self._session_id, self._segment_index)
            return None
        self._segment_index += 1
        self._committed.append(seg)
        await self._broadcast(ev.transcript_final(
            session_id=self._session_id, segment_id=seg["id"], segment_index=seg["segment_index"],
            start_ms=seg["start_ms"], end_ms=seg["end_ms"], text=seg["source_text"],
            speaker_id=seg["speaker_id"], confidence=seg["confidence"],
        ))
        return seg
