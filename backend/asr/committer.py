"""SentenceCommitter：将 committed 词级 token 流聚为稳定、非重叠的句子（ADR-003）。

内核（LocalAgreement）只保证 token 追加稳定；本组件负责"成句"。
成句边界：句末标点 / 静音间隔 / 句子时长上限 / 会话结束 flush。
输入 token 的 start/end 为相对流的秒（float），输出为录音相对毫秒（int）。
"""
from __future__ import annotations

from typing import Optional

from .base import CommittedSentence

SENTENCE_END_PUNCT = tuple(".!?。！？…")
CLOSING_CHARS = "\"'”’）)]】》」"


class SentenceCommitter:
    def __init__(self, max_gap_ms: int = 800, max_sentence_ms: int = 30000,
                 max_chars: int = 500):
        self.max_gap_ms = max_gap_ms
        self.max_sentence_ms = max_sentence_ms
        self.max_chars = max_chars
        self._words: list[dict] = []
        self._last_end_ms: Optional[int] = None
        self._language: Optional[str] = None
        self._speaker: Optional[str] = None

    def add_token(self, *, text: str, start_s: Optional[float], end_s: Optional[float],
                  probability: Optional[float] = None, is_silence: bool = False,
                  speaker: Optional[str] = None, detected_language: Optional[str] = None,
                  ) -> Optional[CommittedSentence]:
        """喂入一个 committed token；命中成句边界则返回一句，否则返回 None。"""
        start_ms = int((start_s or 0.0) * 1000) if start_s is not None else None
        end_ms = int((end_s or 0.0) * 1000) if end_s is not None else None

        if is_silence:
            # 静音标记：若有缓冲句且间隔足够，提交
            if self._words:
                return self._emit()
            return None

        if not text or not text.strip():
            return None

        # 静音间隔边界：与前一个词间隔过大 → 先提交已有句
        if (self._words and self._last_end_ms is not None and start_ms is not None
                and start_ms - self._last_end_ms >= self.max_gap_ms):
            sentence = self._emit()
            self._append(text, start_ms, end_ms, probability, speaker, detected_language)
            return sentence

        self._append(text, start_ms, end_ms, probability, speaker, detected_language)

        # 句末标点边界
        stripped = text.strip()
        if self._ends_sentence(stripped):
            return self._emit()

        # 长度上限边界（防止长句迟迟不提交）
        cur = self._current_text()
        if len(cur) >= self.max_chars:
            return self._emit()
        if self._words:
            first_start = self._words[0]["start_ms"] or 0
            if (end_ms or 0) - first_start >= self.max_sentence_ms:
                return self._emit()
        return None

    def flush(self) -> Optional[CommittedSentence]:
        """会话结束：强制提交尾部未确认内容。"""
        if self._words:
            return self._emit()
        return None

    # ---- 内部 ----

    def _append(self, text, start_ms, end_ms, probability, speaker, detected_language) -> None:
        self._words.append({
            "text": text, "start_ms": start_ms, "end_ms": end_ms, "prob": probability,
        })
        if end_ms is not None:
            self._last_end_ms = end_ms
        if speaker:
            self._speaker = speaker
        if detected_language:
            self._language = detected_language

    def _current_text(self) -> str:
        return "".join(w["text"] for w in self._words).strip()

    def _emit(self) -> CommittedSentence:
        words = self._words
        self._words = []
        text = "".join(w["text"] for w in words).strip()
        start_ms = next((w["start_ms"] for w in words if w["start_ms"] is not None), 0) or 0
        end_ms = next((w["end_ms"] for w in reversed(words) if w["end_ms"] is not None), start_ms) or start_ms
        probs = [w["prob"] for w in words if w["prob"] is not None]
        confidence = round(sum(probs) / len(probs), 4) if probs else None
        sentence = CommittedSentence(
            text=text, start_ms=start_ms, end_ms=max(end_ms, start_ms),
            confidence=confidence, speaker_id=self._speaker, detected_language=self._language,
        )
        return sentence

    @staticmethod
    def _ends_sentence(stripped_token: str) -> bool:
        if not stripped_token:
            return False
        s = stripped_token.rstrip(CLOSING_CHARS)
        return s.endswith(SENTENCE_END_PUNCT)
