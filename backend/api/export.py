"""导出 API（P1-4）。

GET /api/sessions/{id}/export?format=srt&content=bilingual&with_speaker=false&with_timestamps=true
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ..errors import ErrorInfo, LecturelyError
from ..export.exporters import ContentMode, ExportOptions, export_segments
from ..storage.repositories.sessions import SegmentRepository, SessionRepository

router = APIRouter(tags=["export"])


@router.get("/api/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    format: str = Query("srt"),
    content: str = Query("bilingual"),
    with_speaker: bool = Query(False),
    with_timestamps: bool = Query(True),
) -> Response:
    sess = await SessionRepository().get(session_id)
    if not sess:
        raise LecturelyError(ErrorInfo("export", "session_not_found", "会话不存在"))
    segments = await SegmentRepository().list_by_session(session_id)
    if not segments:
        raise LecturelyError(ErrorInfo("export", "no_segments", "该会话没有可导出的内容"))
    try:
        opts = ExportOptions(
            content=ContentMode(content),
            with_speaker=with_speaker,
            with_timestamps=with_timestamps or format.lower() in ("srt", "vtt"),
        )
    except ValueError:
        raise LecturelyError(ErrorInfo("export", "invalid_content", f"无效的内容模式：{content}"))
    try:
        body, content_type, ext = export_segments(format, segments, opts, title=sess["title"])
    except ValueError as e:
        raise LecturelyError(ErrorInfo("export", "invalid_format", str(e),
                                     suggestion="支持 txt / markdown / csv / srt / vtt"))
    filename = f"{sess['title']}.{ext}".replace("/", "_").replace("\\", "_")
    from urllib.parse import quote
    return Response(
        content=body.encode("utf-8"),
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )
