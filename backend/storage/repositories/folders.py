"""Folder 仓储（P1 文件夹管理）。"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import delete, func, select, update

from ...utils import new_id, now_ms
from .. import models
from ..db import get_session


def _d(row) -> dict[str, Any]:
    return dict(row._mapping) if row is not None else {}


class FolderRepository:
    async def create(self, name: str) -> dict[str, Any]:
        now = now_ms()
        values = dict(id=new_id(), name=name, created_at=now, updated_at=now)
        async with get_session()() as s:
            await s.execute(models.folders.insert().values(**values))
            await s.commit()
        return values

    async def get(self, folder_id: str) -> Optional[dict[str, Any]]:
        async with get_session()() as s:
            row = (await s.execute(select(models.folders).where(models.folders.c.id == folder_id))).first()
        return _d(row) if row else None

    async def list(self) -> list[dict[str, Any]]:
        async with get_session()() as s:
            rows = (await s.execute(select(models.folders).order_by(models.folders.c.created_at))).all()
        return [_d(r) for r in rows]

    async def rename(self, folder_id: str, name: str) -> None:
        async with get_session()() as s:
            await s.execute(
                update(models.folders).where(models.folders.c.id == folder_id)
                .values(name=name, updated_at=now_ms())
            )
            await s.commit()

    async def delete(self, folder_id: str) -> None:
        # sessions.folder_id 为 ON DELETE SET NULL，会话回到"未归档"
        async with get_session()() as s:
            await s.execute(delete(models.folders).where(models.folders.c.id == folder_id))
            await s.commit()

    async def list_with_counts(self) -> list[dict[str, Any]]:
        async with get_session()() as s:
            rows = (await s.execute(
                select(
                    models.folders,
                    func.count(models.sessions.c.id).label("session_count"),
                )
                .outerjoin(models.sessions, models.sessions.c.folder_id == models.folders.c.id)
                .group_by(models.folders.c.id)
                .order_by(models.folders.c.created_at)
            )).all()
        result = []
        for r in rows:
            d = _d(r)
            d["session_count"] = r._mapping["session_count"]
            result.append(d)
        return result
