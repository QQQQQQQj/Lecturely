"""AI API（M14 纪要 / M15 问答 / Provider 测试）。

POST /api/sessions/{id}/summary          生成最终纪要（异步任务）
GET  /api/sessions/{id}/summary          获取最终纪要
GET  /api/ai/tasks/{task_id}             纪要任务状态
POST /api/sessions/{id}/notes/realtime   更新实时增量纪要
GET  /api/sessions/{id}/notes            获取实时纪要
POST /api/ai/test                        测试 AI Provider 连通性
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..ai.base import available_ai_providers, create_ai_provider
from ..core.ai_manager import get_ai_manager
from ..errors import ErrorInfo, LecturelyError
from ..storage.repositories.sessions import SegmentRepository

router = APIRouter(tags=["ai"])


@router.post("/api/sessions/{session_id}/summary")
async def generate_summary(session_id: str) -> dict:
    task = await get_ai_manager().generate_summary(session_id)
    return task.to_dict()


@router.get("/api/sessions/{session_id}/summary")
async def get_summary(session_id: str) -> dict:
    summary = await get_ai_manager().get_summary(session_id, "final")
    if not summary:
        return {"exists": False, "content": None}
    return {"exists": True, **summary}


@router.get("/api/ai/tasks/{task_id}")
async def get_ai_task(task_id: str) -> dict:
    task = get_ai_manager().get_task(task_id)
    if not task:
        raise LecturelyError(ErrorInfo("ai", "task_not_found", "任务不存在"))
    return task


@router.post("/api/sessions/{session_id}/notes/realtime")
async def update_realtime_note(session_id: str) -> dict:
    # 默认用全部段做增量（增量合并由 summarizer 处理）
    segments = await SegmentRepository().list_by_session(session_id)
    content = await get_ai_manager().update_realtime_note(session_id, segments)
    return {"content": content}


@router.get("/api/sessions/{session_id}/notes")
async def get_realtime_note(session_id: str) -> dict:
    note = await get_ai_manager().get_summary(session_id, "realtime")
    if not note:
        return {"exists": False, "content": None}
    return {"exists": True, **note}


class QARequest(BaseModel):
    question: str


@router.post("/api/sessions/{session_id}/qa")
async def ask_question(session_id: str, payload: QARequest) -> dict:
    """会话内 AI 问答（RAG + 引用）。"""
    return await get_ai_manager().answer_question(session_id, payload.question)


@router.get("/api/sessions/{session_id}/qa")
async def get_qa_history(session_id: str) -> dict:
    return {"messages": await get_ai_manager().get_qa_history(session_id)}


@router.delete("/api/sessions/{session_id}/qa")
async def clear_qa_history(session_id: str) -> dict:
    await get_ai_manager().clear_qa_history(session_id)
    return {"ok": True}


@router.get("/api/ai/providers")
async def list_ai_providers() -> dict:
    return {"providers": available_ai_providers()}


@router.post("/api/ai/test")
async def test_ai(payload: dict[str, Any]) -> dict:
    provider_name = payload.get("provider", "ollama")
    config = payload.get("config", {})
    try:
        provider = create_ai_provider(provider_name, config)
        health = await provider.health()
        return {"ok": health.ok, "latency_ms": health.latency_ms, "message": health.message}
    except LecturelyError as e:
        return {"ok": False, "message": e.info.message, "suggestion": e.info.suggestion}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
