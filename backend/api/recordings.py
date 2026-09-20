"""录音音频服务 API（P0-10 音频播放器）。

GET /api/recordings/{session_id}/audio  流式返回 WAV（支持 Range 拖动）
GET /api/recordings/{session_id}        录音元信息
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..config import get_config
from ..errors import ErrorInfo, LecturelyError
from ..storage.repositories.misc import RecordingRepository

router = APIRouter(tags=["recordings"])


@router.get("/api/recordings/{session_id}")
async def get_recording(session_id: str) -> dict:
    recording = await RecordingRepository().get_by_session(session_id)
    if not recording:
        raise LecturelyError(ErrorInfo("recording", "recording_not_found", "该会话没有录音"))
    cfg = get_config()
    path = (cfg.recordings_dir / recording["file_path"]).resolve()
    recording["file_exists"] = path.is_file() and str(path).startswith(str(cfg.recordings_dir.resolve()))
    return recording


@router.get("/api/recordings/{session_id}/audio")
async def get_recording_audio(session_id: str):
    recording = await RecordingRepository().get_by_session(session_id)
    if not recording:
        raise LecturelyError(ErrorInfo("recording", "recording_not_found", "该会话没有录音"))
    cfg = get_config()
    path = (cfg.recordings_dir / recording["file_path"]).resolve()
    # 防路径穿越
    if not str(path).startswith(str(cfg.recordings_dir.resolve())):
        raise LecturelyError(ErrorInfo("recording", "invalid_path", "非法录音路径"))
    if not path.is_file():
        raise LecturelyError(ErrorInfo(
            "recording", "recording_file_missing",
            "录音文件不存在（可能已被删除或移动）",
            suggestion="可重新录制，或检查数据目录"))
    # FileResponse 支持 Range（拖动进度）
    return FileResponse(str(path), media_type="audio/wav", filename=recording["file_path"])
