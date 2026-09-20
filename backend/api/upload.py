"""文件上传转录 API（P0-11）。

POST /api/upload              multipart 上传音频/视频 → 创建转录任务
GET  /api/upload/tasks        任务列表（进度/状态）
GET  /api/upload/tasks/{id}   任务详情
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile

from ..config import get_config
from ..core.upload_manager import get_upload_manager
from ..errors import ErrorInfo, LecturelyError
from ..logging_setup import get_logger
from ..utils import new_id

logger = get_logger(__name__)
router = APIRouter(tags=["upload"])


@router.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    source_language: str = Form("auto"),
    target_language: str = Form("zh-CN"),
    folder_id: str = Form(""),
) -> dict:
    cfg = get_config()
    manager = get_upload_manager(cfg)
    original_name = file.filename or "upload"
    ext = Path(original_name).suffix.lower()

    # 保存到 data/uploads/（安全文件名，防路径穿越）
    safe_name = f"{new_id()}{ext}"
    dest = cfg.uploads_dir / safe_name
    size = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB 块
                if not chunk:
                    break
                size += len(chunk)
                if size > cfg.upload_max_mb * 1024 * 1024:
                    f.close()
                    dest.unlink(missing_ok=True)
                    raise LecturelyError(ErrorInfo(
                        "upload", "file_too_large",
                        f"文件超过大小上限（{cfg.upload_max_mb}MB）",
                        suggestion="请上传更小的文件"))
                f.write(chunk)
    except LecturelyError:
        raise
    except Exception as e:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise LecturelyError(ErrorInfo("upload", "save_failed", f"文件保存失败：{e}")) from e
    finally:
        await file.close()

    # 校验格式
    manager.validate_file(original_name, size)
    logger.info("文件已上传：%s（%d 字节）→ %s", original_name, size, safe_name)

    task = await manager.submit(
        file_path=str(dest), file_name=original_name,
        source_language=source_language, target_language=target_language,
        folder_id=folder_id or None,
    )
    return task.to_dict()


@router.get("/api/upload/tasks")
async def list_upload_tasks() -> dict:
    manager = get_upload_manager(get_config())
    return {"tasks": manager.list_tasks()}


@router.get("/api/upload/tasks/{task_id}")
async def get_upload_task(task_id: str) -> dict:
    manager = get_upload_manager(get_config())
    task = manager.get_task(task_id)
    if not task:
        raise LecturelyError(ErrorInfo("upload", "task_not_found", "任务不存在"))
    return task
