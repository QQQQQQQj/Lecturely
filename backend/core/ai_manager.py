"""AIManager：AI 纪要与问答任务编排（对应 M14/M15）。

- 最终纪要：异步任务生成（分块→合并），状态可轮询，结果存 summaries(type=final)。
- 实时增量纪要：已有纪要 + 新增段 → 合并更新（不重复发全文），存 summaries(type=realtime)。
- 问答：基于当前 Session 的转录内容回答（M15）。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from ..ai.base import AIProvider, ChatMessage
from ..ai.base import create_ai_provider
from ..ai.qa import Embedder, answer_question as qa_answer
from ..ai.summarizer import generate_final_summary, generate_realtime_note
from ..errors import ErrorInfo, LecturelyError, ProviderError
from ..logging_setup import get_logger
from ..storage.repositories.misc import AiMessageRepository, SummaryRepository
from ..storage.repositories.sessions import SegmentRepository, SessionRepository
from ..utils import new_id
from .app_settings import get_ai_config, get_provider_and_config

logger = get_logger(__name__)


class SummaryTask:
    def __init__(self, task_id: str, session_id: str):
        self.task_id = task_id
        self.session_id = session_id
        self.status = "processing"      # processing | completed | failed
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "session_id": self.session_id,
                "status": self.status, "error": self.error}


class AIManager:
    def __init__(self):
        self._tasks: dict[str, SummaryTask] = {}
        self._lock = asyncio.Lock()

    async def _provider(self) -> AIProvider:
        name, cfg = await get_provider_and_config("ai")
        return create_ai_provider(name, cfg)

    def get_task(self, task_id: str) -> Optional[dict]:
        t = self._tasks.get(task_id)
        return t.to_dict() if t else None

    async def generate_summary(self, session_id: str) -> SummaryTask:
        sess = await SessionRepository().get(session_id)
        if not sess:
            raise LecturelyError(ErrorInfo("ai", "session_not_found", "会话不存在"))
        segments = await SegmentRepository().list_by_session(session_id)
        if not segments:
            raise LecturelyError(ErrorInfo("ai", "no_segments", "该会话没有转录内容，无法生成纪要"))
        task = SummaryTask(new_id(), session_id)
        self._tasks[task.task_id] = task
        asyncio.create_task(self._run_summary(task, segments, sess.get("target_language", "zh-CN")))
        return task

    async def _run_summary(self, task: SummaryTask, segments: list[dict], target_language: str) -> None:
        async with self._lock:  # 串行（避免并发压垮本地 LLM）
            try:
                provider = await self._provider()
                content = await generate_final_summary(provider, segments, target_language=target_language)
                await SummaryRepository().upsert(
                    session_id=task.session_id, type="final", content=content,
                    provider=provider.name, model=getattr(provider, "_model", ""),
                )
                task.status = "completed"
                logger.info("纪要生成完成：session=%s", task.session_id)
            except ProviderError as e:
                task.status = "failed"
                task.error = e.info.message
                logger.warning("纪要生成失败：%s", e.info.message)
            except Exception as e:  # noqa: BLE001
                task.status = "failed"
                task.error = str(e)
                logger.warning("纪要生成异常：%s", e)

    async def get_summary(self, session_id: str, type: str = "final") -> Optional[dict]:
        return await SummaryRepository().get(session_id, type)

    async def update_realtime_note(self, session_id: str, new_segments: list[dict]) -> str:
        """实时增量纪要：读取已有 realtime 纪要 + 新增段 → 合并更新并存储。"""
        existing = await SummaryRepository().get(session_id, "realtime")
        existing_text = existing["content"] if existing else ""
        provider = await self._provider()
        content = await generate_realtime_note(provider, existing_note=existing_text,
                                               new_segments=new_segments)
        await SummaryRepository().upsert(
            session_id=session_id, type="realtime", content=content,
            provider=provider.name, model=getattr(provider, "_model", ""),
        )
        return content

    async def answer_question(self, session_id: str, question: str) -> dict:
        """M15：基于会话内容的 RAG 问答，返回 {answer, citations} 并存消息历史。"""
        sess = await SessionRepository().get(session_id)
        if not sess:
            raise LecturelyError(ErrorInfo("ai", "session_not_found", "会话不存在"))
        segments = await SegmentRepository().list_by_session(session_id)
        if not question.strip():
            raise LecturelyError(ErrorInfo("ai", "question_empty", "问题不能为空"))

        provider = await self._provider()
        ai_cfg = await get_ai_config()
        embed_cfg = ai_cfg.get("embedding", {})
        embedder = Embedder(
            endpoint=embed_cfg.get("endpoint", "http://127.0.0.1:11434"),
            model=embed_cfg.get("model", "bge-m3"),
        )
        # 历史消息（上下文）
        history_rows = await AiMessageRepository().list_by_session(session_id)
        history = [{"role": r["role"], "content": r["content"]} for r in history_rows[-6:]]

        result = await qa_answer(
            provider=provider, embedder=embedder, session_id=session_id,
            segments=segments, question=question.strip(), history=history,
        )
        # 存消息历史
        await AiMessageRepository().add(session_id=session_id, role="user", content=question.strip())
        await AiMessageRepository().add(session_id=session_id, role="assistant",
                                        content=result["answer"], citations=result.get("citations"))
        return result

    async def get_qa_history(self, session_id: str) -> list[dict]:
        return await AiMessageRepository().list_by_session(session_id)

    async def clear_qa_history(self, session_id: str) -> None:
        await AiMessageRepository().clear(session_id)


_ai_manager: Optional[AIManager] = None


def get_ai_manager() -> AIManager:
    global _ai_manager
    if _ai_manager is None:
        _ai_manager = AIManager()
    return _ai_manager
