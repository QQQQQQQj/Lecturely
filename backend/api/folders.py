"""Folder REST API（P1-1 文件夹管理）。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..errors import ErrorInfo, LecturelyError
from ..storage.repositories.folders import FolderRepository

router = APIRouter(tags=["folders"])


@router.get("/api/folders")
async def list_folders() -> dict:
    folders = await FolderRepository().list_with_counts()
    return {"folders": folders}


class FolderCreate(BaseModel):
    name: str


@router.post("/api/folders")
async def create_folder(payload: FolderCreate) -> dict:
    name = payload.name.strip()
    if not name:
        raise LecturelyError(ErrorInfo("folder", "folder_name_empty", "文件夹名称不能为空"))
    return await FolderRepository().create(name)


class FolderPatch(BaseModel):
    name: str


@router.patch("/api/folders/{folder_id}")
async def rename_folder(folder_id: str, payload: FolderPatch) -> dict:
    name = payload.name.strip()
    if not name:
        raise LecturelyError(ErrorInfo("folder", "folder_name_empty", "文件夹名称不能为空"))
    repo = FolderRepository()
    if not await repo.get(folder_id):
        raise LecturelyError(ErrorInfo("folder", "folder_not_found", "文件夹不存在"))
    await repo.rename(folder_id, name)
    return await repo.get(folder_id)


@router.delete("/api/folders/{folder_id}")
async def delete_folder(folder_id: str) -> dict:
    repo = FolderRepository()
    if not await repo.get(folder_id):
        raise LecturelyError(ErrorInfo("folder", "folder_not_found", "文件夹不存在"))
    await repo.delete(folder_id)  # 其中会话回到"未归档"（SET NULL）
    return {"ok": True, "deleted": folder_id}
