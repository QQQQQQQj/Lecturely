"""SearchManager：全局搜索（P1-5）。

搜索范围：Session 标题、Segment 原文、Segment 译文、文件夹名。
采用 LIKE 子串匹配（对中英文均可靠；FTS5 默认分词对中文不友好，故不用）。
结果按会话分组，附命中文本上下文（供前端高亮/定位）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select

from ..storage import models
from ..storage.db import get_session


def _snippet(text: str, query: str, width: int = 60) -> str:
    """提取命中位置上下文。"""
    idx = text.lower().find(query.lower())
    if idx < 0:
        return text[:width]
    start = max(0, idx - width // 3)
    end = min(len(text), idx + len(query) + width // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


async def search(query: str, limit: int = 50) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"query": q, "sessions": [], "segment_hits": [], "folder_hits": []}
    like = f"%{q}%"

    # 1. 标题匹配的会话
    async with get_session()() as s:
        title_rows = (await s.execute(
            select(models.sessions).where(models.sessions.c.title.like(like))
            .order_by(models.sessions.c.started_at.desc()).limit(limit)
        )).all()
        # 2. 文件夹名匹配
        folder_rows = (await s.execute(
            select(models.folders).where(models.folders.c.name.like(like)).limit(20)
        )).all()
        # 3. 段内容匹配（原文/译文）
        seg_rows = (await s.execute(
            select(
                models.transcript_segments.c.id,
                models.transcript_segments.c.session_id,
                models.transcript_segments.c.segment_index,
                models.transcript_segments.c.start_ms,
                models.transcript_segments.c.source_text,
                models.transcript_segments.c.translated_text,
                models.sessions.c.title.label("session_title"),
            )
            .join(models.sessions, models.sessions.c.id == models.transcript_segments.c.session_id)
            .where(or_(
                models.transcript_segments.c.source_text.like(like),
                models.transcript_segments.c.translated_text.like(like),
            ))
            .order_by(models.sessions.c.started_at.desc())
            .limit(limit)
        )).all()

    title_hits = [dict(r._mapping) for r in title_rows]
    title_hit_ids = {r["id"] for r in title_hits}

    segment_hits = []
    for r in seg_rows:
        m = r._mapping
        src = m["source_text"] or ""
        tgt = m["translated_text"] or ""
        matched = "source" if q.lower() in src.lower() else "translation"
        segment_hits.append({
            "segment_id": m["id"],
            "session_id": m["session_id"],
            "session_title": m["session_title"],
            "segment_index": m["segment_index"],
            "start_ms": m["start_ms"],
            "matched_field": matched,
            "snippet": _snippet(src if matched == "source" else tgt, q),
        })

    folder_hits = [dict(r._mapping) for r in folder_rows]

    return {
        "query": q,
        "sessions": title_hits,
        "segment_hits": segment_hits,
        "folder_hits": folder_hits,
        "title_hit_ids": list(title_hit_ids),
    }
