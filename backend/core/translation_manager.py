"""TranslationManager：实时翻译调度（借鉴 LiveCaptions-Translator）。

策略：
- 每个 committed segment 提交一个翻译任务，worker 串行处理（避免压垮本地服务）。
- 译文 UPDATE 同一 segment 行（translated_text），并广播 translation.final。
- 失败置 translation_failed 且**绝不阻塞原文落库与下一句**；可经 translation.retry 重试。
- 上下文：LLM 系使用最近 N 句 (原文,译文) few-shot；mtran 不支持上下文。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from ..errors import ProviderError
from ..logging_setup import get_logger
from ..storage.repositories.sessions import SegmentRepository
from ..translation import TranslateRequest, TranslationProvider, create_provider
from ..translation.context import TranslationContext
from ..websocket import events as ev

logger = get_logger(__name__)

Broadcast = Callable[[dict], Awaitable[None]]


class TranslationManager:
    def __init__(self, *, provider: TranslationProvider, target_language: str,
                 source_language: str, session_id: str, broadcast: Broadcast,
                 context_turns: int = 2):
        self._provider = provider
        self._target_language = target_language
        self._source_language = source_language
        self._session_id = session_id
        self._broadcast = broadcast
        self._repo = SegmentRepository()
        self._context = TranslationContext(context_turns)
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = asyncio.create_task(self._loop())

    async def submit(self, segment: dict) -> None:
        """提交一个 committed segment 进行翻译。"""
        if not self._running:
            self.start()
        await self._queue.put(segment)

    async def retry(self, segment_id: str) -> None:
        seg = await self._repo.get(segment_id)
        if seg and seg.get("source_text"):
            await self._repo.set_status(segment_id, "committed")
            await self.submit(seg)

    async def _loop(self) -> None:
        while self._running:
            try:
                seg = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            try:
                await self._translate_one(seg)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("翻译任务异常：%s", e)

    async def _translate_one(self, seg: dict) -> None:
        segment_id = seg["id"]
        source_text = seg.get("source_text", "")
        if not source_text.strip():
            return
        req = TranslateRequest(
            text=source_text,
            source_lang=seg.get("source_language") or self._source_language,
            target_lang=self._target_language,
            context=self._context.get() if self._provider.supports_context() else [],
        )
        try:
            result = await self._provider.translate(req)
            translated = result.text
            await self._repo.set_translation(segment_id, translated, status="translated")
            self._context.add(source_text, translated)
            await self._broadcast(ev.translation_final(
                session_id=self._session_id, segment_id=segment_id,
                text=translated, provider=result.provider,
            ))
            logger.debug("翻译完成 segment=%s", segment_id)
        except ProviderError as e:
            await self._repo.set_status(segment_id, "translation_failed")
            await self._broadcast(ev.make_event(ev.TRANSLATION_FAILED, session_id=self._session_id, payload={
                "segment_id": segment_id, "error": e.info.code,
                "message": e.info.message, "suggestion": e.info.suggestion,
                "retryable": e.info.retryable,
            }))
            logger.warning("翻译失败 segment=%s：%s", segment_id, e.info.message)
        except Exception as e:  # noqa: BLE001
            await self._repo.set_status(segment_id, "translation_failed")
            await self._broadcast(ev.make_event(ev.TRANSLATION_FAILED, session_id=self._session_id, payload={
                "segment_id": segment_id, "error": "unknown",
                "message": f"翻译失败：{e}", "retryable": True,
            }))
            logger.warning("翻译异常 segment=%s：%s", segment_id, e)

    async def drain(self, timeout: float = 10) -> None:
        """停止前等待在途翻译完成（超时不再等待）。"""
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("翻译队列排空超时，剩余 %d 条标记失败", self._queue.qsize())

    async def stop(self) -> None:
        self._running = False
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None


def build_translation_manager(*, provider_name: str, provider_config: dict[str, Any],
                              target_language: str, source_language: str,
                              session_id: str, broadcast: Broadcast) -> TranslationManager:
    provider = create_provider(provider_name, provider_config)
    return TranslationManager(
        provider=provider, target_language=target_language, source_language=source_language,
        session_id=session_id, broadcast=broadcast,
    )
