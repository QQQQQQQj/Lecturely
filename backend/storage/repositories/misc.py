"""Recording / Summary / AiMessage / Settings 仓储。"""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import delete, select, update

from ...utils import new_id, now_ms
from .. import models
from ..db import get_session


def _d(row) -> dict[str, Any]:
    return dict(row._mapping) if row is not None else {}


class RecordingRepository:
    async def create(self, *, session_id: str, file_path: str, format: str = "wav",
                     sample_rate: int = 16000, channels: int = 1) -> dict[str, Any]:
        values = dict(id=new_id(), session_id=session_id, file_path=file_path, format=format,
                      sample_rate=sample_rate, channels=channels, size_bytes=0, duration_ms=0,
                      created_at=now_ms())
        async with get_session()() as s:
            await s.execute(models.recordings.insert().values(**values))
            await s.commit()
        return values

    async def get_by_session(self, session_id: str) -> Optional[dict[str, Any]]:
        async with get_session()() as s:
            row = (await s.execute(
                select(models.recordings).where(models.recordings.c.session_id == session_id)
            )).first()
        return _d(row) if row else None

    async def finalize(self, recording_id: str, *, size_bytes: int, duration_ms: int) -> None:
        async with get_session()() as s:
            await s.execute(
                update(models.recordings).where(models.recordings.c.id == recording_id)
                .values(size_bytes=size_bytes, duration_ms=duration_ms)
            )
            await s.commit()

    async def delete_by_session(self, session_id: str) -> None:
        async with get_session()() as s:
            await s.execute(delete(models.recordings).where(models.recordings.c.session_id == session_id))
            await s.commit()


class SummaryRepository:
    async def upsert(self, *, session_id: str, type: str, content: str,
                     provider: Optional[str] = None, model: Optional[str] = None) -> dict[str, Any]:
        now = now_ms()
        async with get_session()() as s:
            row = (await s.execute(
                select(models.summaries).where(
                    models.summaries.c.session_id == session_id,
                    models.summaries.c.type == type,
                )
            )).first()
            if row:
                sid = row._mapping["id"]
                await s.execute(
                    update(models.summaries).where(models.summaries.c.id == sid)
                    .values(content=content, provider=provider, model=model, updated_at=now)
                )
            else:
                sid = new_id()
                await s.execute(models.summaries.insert().values(
                    id=sid, session_id=session_id, type=type, content=content,
                    provider=provider, model=model, created_at=now, updated_at=now,
                ))
            await s.commit()
        return dict(id=sid, session_id=session_id, type=type, content=content,
                    provider=provider, model=model, created_at=now, updated_at=now)

    async def get(self, session_id: str, type: str) -> Optional[dict[str, Any]]:
        async with get_session()() as s:
            row = (await s.execute(
                select(models.summaries).where(
                    models.summaries.c.session_id == session_id,
                    models.summaries.c.type == type,
                )
            )).first()
        return _d(row) if row else None

    async def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        async with get_session()() as s:
            rows = (await s.execute(
                select(models.summaries).where(models.summaries.c.session_id == session_id)
            )).all()
        return [_d(r) for r in rows]

    async def has_summary(self, session_id: str) -> bool:
        async with get_session()() as s:
            row = (await s.execute(
                select(models.summaries.c.id).where(models.summaries.c.session_id == session_id).limit(1)
            )).first()
        return row is not None


class AiMessageRepository:
    async def add(self, *, session_id: str, role: str, content: str,
                  citations: Optional[list[dict]] = None) -> dict[str, Any]:
        values = dict(id=new_id(), session_id=session_id, role=role, content=content,
                      citations_json=json.dumps(citations, ensure_ascii=False) if citations else None,
                      created_at=now_ms())
        async with get_session()() as s:
            await s.execute(models.ai_messages.insert().values(**values))
            await s.commit()
        return values

    async def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        async with get_session()() as s:
            rows = (await s.execute(
                select(models.ai_messages)
                .where(models.ai_messages.c.session_id == session_id)
                .order_by(models.ai_messages.c.created_at)
            )).all()
        return [_d(r) for r in rows]

    async def clear(self, session_id: str) -> None:
        async with get_session()() as s:
            await s.execute(delete(models.ai_messages).where(models.ai_messages.c.session_id == session_id))
            await s.commit()


class SpeakerRepository:
    async def upsert(self, *, session_id: str, label: str, display_name: str) -> dict[str, Any]:
        now = now_ms()
        async with get_session()() as s:
            row = (await s.execute(
                select(models.speakers).where(
                    models.speakers.c.session_id == session_id,
                    models.speakers.c.label == label,
                )
            )).first()
            if row:
                sid = row._mapping["id"]
                await s.execute(
                    update(models.speakers).where(models.speakers.c.id == sid)
                    .values(display_name=display_name)
                )
            else:
                sid = new_id()
                await s.execute(models.speakers.insert().values(
                    id=sid, session_id=session_id, label=label,
                    display_name=display_name, created_at=now,
                ))
            await s.commit()
        return dict(id=sid, session_id=session_id, label=label, display_name=display_name)

    async def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        async with get_session()() as s:
            rows = (await s.execute(
                select(models.speakers).where(models.speakers.c.session_id == session_id)
            )).all()
        return [_d(r) for r in rows]

    async def rename_segments(self, session_id: str, label: str, new_speaker_id: str) -> None:
        """将会话中某 speaker_id 的段统一改为新 speaker_id（手动归并/重命名）。"""
        async with get_session()() as s:
            await s.execute(
                update(models.transcript_segments)
                .where(
                    models.transcript_segments.c.session_id == session_id,
                    models.transcript_segments.c.speaker_id == label,
                )
                .values(speaker_id=new_speaker_id, updated_at=now_ms())
            )
            await s.commit()


class SettingsRepository:
    async def get(self, key: str, default: Any = None) -> Any:
        async with get_session()() as s:
            row = (await s.execute(select(models.settings).where(models.settings.c.key == key))).first()
        if not row:
            return default
        try:
            return json.loads(row._mapping["value_json"])
        except Exception:
            return default

    async def set(self, key: str, value: Any) -> None:
        now = now_ms()
        payload = json.dumps(value, ensure_ascii=False)
        async with get_session()() as s:
            row = (await s.execute(select(models.settings).where(models.settings.c.key == key))).first()
            if row:
                await s.execute(
                    update(models.settings).where(models.settings.c.key == key)
                    .values(value_json=payload, updated_at=now)
                )
            else:
                await s.execute(models.settings.insert().values(
                    key=key, value_json=payload, updated_at=now,
                ))
            await s.commit()

    async def get_all(self) -> dict[str, Any]:
        async with get_session()() as s:
            rows = (await s.execute(select(models.settings))).all()
        result = {}
        for r in rows:
            try:
                result[r._mapping["key"]] = json.loads(r._mapping["value_json"])
            except Exception:
                pass
        return result

    async def delete(self, key: str) -> None:
        async with get_session()() as s:
            await s.execute(delete(models.settings).where(models.settings.c.key == key))
            await s.commit()
