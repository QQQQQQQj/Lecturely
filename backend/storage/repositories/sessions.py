"""Session 与 TranscriptSegment 仓储。

关键纪律（ADR-003/005）：
- commit_segment 幂等：UNIQUE(session_id, segment_index) + INSERT OR IGNORE。
- 翻译写同一行：UPDATE translated_text。
- segment_count 由提交时事务内维护。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import delete, func, select, update

from ...utils import new_id, now_ms
from .. import models
from ..db import get_session


def _row_to_dict(row) -> dict[str, Any]:
    return dict(row._mapping) if row is not None else {}


class SessionRepository:
    async def create(self, *, title: str, source_language: str, target_language: str,
                     input_mode: str, folder_id: Optional[str] = None,
                     started_at: Optional[int] = None, status: str = "recording",
                     session_id: Optional[str] = None) -> dict[str, Any]:
        now = now_ms()
        sid = session_id or new_id()
        values = dict(
            id=sid, folder_id=folder_id, title=title,
            source_language=source_language, target_language=target_language,
            input_mode=input_mode, status=status,
            started_at=started_at if started_at is not None else now,
            ended_at=None, duration_ms=0, recording_id=None, segment_count=0,
            created_at=now, updated_at=now,
        )
        async with get_session()() as s:
            await s.execute(models.sessions.insert().values(**values))
            await s.commit()
        return values

    async def get(self, session_id: str) -> Optional[dict[str, Any]]:
        async with get_session()() as s:
            row = (await s.execute(
                select(models.sessions).where(models.sessions.c.id == session_id)
            )).first()
        return _row_to_dict(row) if row else None

    async def list(self, *, folder_id: Optional[str] = None, status: Optional[str] = None,
                   limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        q = select(models.sessions).order_by(models.sessions.c.started_at.desc())
        if folder_id is not None:
            q = q.where(models.sessions.c.folder_id == folder_id)
        if status is not None:
            q = q.where(models.sessions.c.status == status)
        q = q.limit(limit).offset(offset)
        async with get_session()() as s:
            rows = (await s.execute(q)).all()
        return [_row_to_dict(r) for r in rows]

    async def update_status(self, session_id: str, status: str, **fields) -> None:
        fields["status"] = status
        fields["updated_at"] = now_ms()
        async with get_session()() as s:
            await s.execute(
                update(models.sessions).where(models.sessions.c.id == session_id).values(**fields)
            )
            await s.commit()

    async def update_fields(self, session_id: str, **fields) -> None:
        fields["updated_at"] = now_ms()
        async with get_session()() as s:
            await s.execute(
                update(models.sessions).where(models.sessions.c.id == session_id).values(**fields)
            )
            await s.commit()

    async def rename(self, session_id: str, title: str) -> None:
        await self.update_fields(session_id, title=title)

    async def move_to_folder(self, session_id: str, folder_id: Optional[str]) -> None:
        await self.update_fields(session_id, folder_id=folder_id)

    async def delete(self, session_id: str) -> None:
        async with get_session()() as s:
            await s.execute(delete(models.sessions).where(models.sessions.c.id == session_id))
            await s.commit()

    async def list_interrupted(self) -> list[dict[str, Any]]:
        """启动时扫描未完成会话（崩溃恢复）。"""
        async with get_session()() as s:
            rows = (await s.execute(
                select(models.sessions).where(
                    models.sessions.c.status.in_(["recording", "paused", "processing"])
                )
            )).all()
        return [_row_to_dict(r) for r in rows]

    async def count(self) -> int:
        async with get_session()() as s:
            return int((await s.execute(select(func.count()).select_from(models.sessions))).scalar_one())


class SegmentRepository:
    async def commit(self, *, session_id: str, segment_index: int, start_ms: int, end_ms: int,
                     source_text: str, source_language: str, target_language: str,
                     speaker_id: Optional[str] = None, confidence: Optional[float] = None,
                     segment_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        """幂等写入 Final segment。已存在同 (session_id, segment_index) 则返回 None（跳过）。"""
        now = now_ms()
        sid = segment_id or new_id()
        values = dict(
            id=sid, session_id=session_id, segment_index=segment_index,
            speaker_id=speaker_id, start_ms=start_ms, end_ms=end_ms,
            source_language=source_language, target_language=target_language,
            source_text=source_text, translated_text=None, confidence=confidence,
            status="committed", created_at=now, updated_at=now,
        )
        async with get_session()() as s:
            result = await s.execute(
                models.transcript_segments.insert().prefix_with("OR IGNORE").values(**values)
            )
            inserted = result.rowcount > 0
            if inserted:
                await s.execute(
                    update(models.sessions)
                    .where(models.sessions.c.id == session_id)
                    .values(segment_count=models.sessions.c.segment_count + 1, updated_at=now)
                )
            await s.commit()
        return values if inserted else None

    async def set_translation(self, segment_id: str, translated_text: str, status: str = "translated") -> None:
        async with get_session()() as s:
            await s.execute(
                update(models.transcript_segments)
                .where(models.transcript_segments.c.id == segment_id)
                .values(translated_text=translated_text, status=status, updated_at=now_ms())
            )
            await s.commit()

    async def set_status(self, segment_id: str, status: str) -> None:
        async with get_session()() as s:
            await s.execute(
                update(models.transcript_segments)
                .where(models.transcript_segments.c.id == segment_id)
                .values(status=status, updated_at=now_ms())
            )
            await s.commit()

    async def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        async with get_session()() as s:
            rows = (await s.execute(
                select(models.transcript_segments)
                .where(models.transcript_segments.c.session_id == session_id)
                .order_by(models.transcript_segments.c.segment_index)
            )).all()
        return [_row_to_dict(r) for r in rows]

    async def get(self, segment_id: str) -> Optional[dict[str, Any]]:
        async with get_session()() as s:
            row = (await s.execute(
                select(models.transcript_segments).where(models.transcript_segments.c.id == segment_id)
            )).first()
        return _row_to_dict(row) if row else None

    async def update_text(self, segment_id: str, source_text: Optional[str] = None,
                          translated_text: Optional[str] = None) -> None:
        fields: dict[str, Any] = {"updated_at": now_ms()}
        if source_text is not None:
            fields["source_text"] = source_text
        if translated_text is not None:
            fields["translated_text"] = translated_text
        async with get_session()() as s:
            await s.execute(
                update(models.transcript_segments)
                .where(models.transcript_segments.c.id == segment_id).values(**fields)
            )
            await s.commit()

    async def count_by_session(self, session_id: str) -> int:
        async with get_session()() as s:
            return int((await s.execute(
                select(func.count()).select_from(models.transcript_segments)
                .where(models.transcript_segments.c.session_id == session_id)
            )).scalar_one())
