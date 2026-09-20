"""Session REST API（P0-8 历史记录 / P0-9 详情页）。

- GET    /api/sessions                 列表（可按 folder_id/status 过滤）
- GET    /api/sessions/{id}            详情（含 has_recording/has_summary）
- PATCH  /api/sessions/{id}            重命名 / 移动文件夹
- DELETE /api/sessions/{id}            删除（可选连录音一起删）
- GET    /api/sessions/{id}/segments   转录段列表
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..config import get_config
from ..errors import ErrorInfo, LecturelyError
from ..storage.repositories.misc import RecordingRepository, SummaryRepository
from ..storage.repositories.sessions import SegmentRepository, SessionRepository

router = APIRouter(tags=["sessions"])


async def _enrich(sess: dict) -> dict:
    sid = sess["id"]
    recording = await RecordingRepository().get_by_session(sid)
    has_summary = await SummaryRepository().has_summary(sid)
    sess["has_recording"] = recording is not None
    sess["has_summary"] = has_summary
    if recording:
        sess["recording"] = recording
    return sess


@router.get("/api/sessions")
async def list_sessions(folder_id: Optional[str] = None, status: Optional[str] = None,
                        limit: int = Query(200, le=500), offset: int = 0) -> dict:
    repo = SessionRepository()
    sessions = await repo.list(folder_id=folder_id, status=status, limit=limit, offset=offset)
    enriched = [await _enrich(s) for s in sessions]
    total = await repo.count()
    return {"sessions": enriched, "total": total}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    sess = await SessionRepository().get(session_id)
    if not sess:
        raise LecturelyError(ErrorInfo("session", "session_not_found", "会话不存在"))
    return await _enrich(sess)


@router.get("/api/sessions/{session_id}/segments")
async def get_segments(session_id: str) -> dict:
    segments = await SegmentRepository().list_by_session(session_id)
    return {"segments": segments}


class SessionPatch(BaseModel):
    title: Optional[str] = None
    folder_id: Optional[str] = None
    move_to_folder: bool = False


@router.patch("/api/sessions/{session_id}")
async def patch_session(session_id: str, payload: SessionPatch) -> dict:
    repo = SessionRepository()
    sess = await repo.get(session_id)
    if not sess:
        raise LecturelyError(ErrorInfo("session", "session_not_found", "会话不存在"))
    if payload.title is not None:
        await repo.rename(session_id, payload.title)
    if payload.move_to_folder:
        await repo.move_to_folder(session_id, payload.folder_id)
    return await _enrich(await repo.get(session_id))


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, delete_recording: bool = False) -> dict:
    repo = SessionRepository()
    sess = await repo.get(session_id)
    if not sess:
        raise LecturelyError(ErrorInfo("session", "session_not_found", "会话不存在"))
    recording_path = None
    if delete_recording:
        recording = await RecordingRepository().get_by_session(session_id)
        if recording:
            recording_path = recording.get("file_path")
    await repo.delete(session_id)  # FK CASCADE 清 segments/recordings/summaries/messages
    # 删除录音文件（防路径穿越：仅允许 data/recordings/ 内的文件）
    if recording_path:
        cfg = get_config()
        try:
            target = (cfg.recordings_dir / recording_path).resolve()
            if target.is_file() and str(target).startswith(str(cfg.recordings_dir.resolve())):
                target.unlink()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "deleted": session_id, "recording_deleted": bool(recording_path)}
