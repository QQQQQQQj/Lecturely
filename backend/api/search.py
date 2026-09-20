"""搜索 API（P1-5）。

GET /api/search?q=...  全局搜索（标题/原文/译文/文件夹）
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..core.search_manager import search

router = APIRouter(tags=["search"])


@router.get("/api/search")
async def global_search(q: str = Query(""), limit: int = Query(50, le=200)) -> dict:
    return await search(q, limit=limit)
