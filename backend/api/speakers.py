"""Speaker API（P1-2：说话人显示与手动重命名）。

注意：当前默认 ASR 不做实时 diarization，speaker_id 为 null（不伪造）。
本 API 用于：查询会话说话人标签、手动重命名（为后续 diarization 或手动归并准备）。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..storage.repositories.misc import SpeakerRepository
from ..storage.repositories.sessions import SegmentRepository

router = APIRouter(tags=["speakers"])


@router.get("/api/sessions/{session_id}/speakers")
async def list_speakers(session_id: str) -> dict:
    # 会话中实际出现的 speaker_id（来自 segments）+ 已命名的标签
    segments = await SegmentRepository().list_by_session(session_id)
    present = sorted({s["speaker_id"] for s in segments if s.get("speaker_id")})
    named = {sp["label"]: sp for sp in await SpeakerRepository().list_by_session(session_id)}
    result = []
    for label in present:
        result.append({
            "label": label,
            "display_name": named.get(label, {}).get("display_name") or label,
        })
    return {"speakers": result, "has_diarization": len(present) > 0}


class SpeakerRename(BaseModel):
    label: str
    display_name: str


@router.put("/api/sessions/{session_id}/speakers")
async def rename_speaker(session_id: str, payload: SpeakerRename) -> dict:
    label = payload.label.strip()
    if not label:
        return {"error": "label 不能为空"}
    sp = await SpeakerRepository().upsert(
        session_id=session_id, label=label, display_name=payload.display_name.strip() or label)
    return sp
