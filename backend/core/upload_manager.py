"""UploadManager：文件上传转录任务（对应目标文档 §P0-11）。

流程：上传 → 创建 Session(input_mode=file) → FileSource 解码 → ASR → SentenceCommitter
→ 分段持久化 → 翻译 → 历史可见。提供进度与失败信息（任务状态可轮询）。
与实时会话复用同一 ASR 引擎（每任务独立 AudioProcessor）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from ..asr.base import ASREventKind, CommittedSentence, EngineStatusEvent, PartialEvent, SessionASRConfig
from ..asr.wlk_engine import WhisperLiveKitASR
from ..audio.file_source import FileSource
from ..config import AppConfig
from ..errors import ErrorInfo, LecturelyError
from ..logging_setup import get_logger
from ..storage.repositories.sessions import SessionRepository
from ..utils import new_id
from .app_settings import get_provider_and_config
from .translation_manager import build_translation_manager
from .transcript_manager import TranscriptManager

logger = get_logger(__name__)

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".mp4", ".mkv", ".mov"}


class UploadTask:
    def __init__(self, task_id: str, session_id: str, file_name: str):
        self.task_id = task_id
        self.session_id = session_id
        self.file_name = file_name
        self.status = "processing"      # processing | completed | failed
        self.progress = 0.0
        self.segment_count = 0
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "session_id": self.session_id,
            "file_name": self.file_name, "status": self.status,
            "progress": round(self.progress, 3), "segment_count": self.segment_count,
            "error": self.error,
        }


class UploadManager:
    def __init__(self, app_cfg: AppConfig, broadcast=None):
        self._app_cfg = app_cfg
        self._broadcast = broadcast or _null_broadcast
        self._tasks: dict[str, UploadTask] = {}
        self._lock = asyncio.Lock()  # 串行执行上传转录（CPU 资源友好）

    def list_tasks(self) -> list[dict]:
        return [t.to_dict() for t in sorted(self._tasks.values(), key=lambda x: x.task_id, reverse=True)]

    def get_task(self, task_id: str) -> Optional[dict]:
        t = self._tasks.get(task_id)
        return t.to_dict() if t else None

    def validate_file(self, filename: str, size_bytes: int) -> None:
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise LecturelyError(ErrorInfo(
                "upload", "unsupported_format",
                f"不支持的文件格式：{ext or '未知'}",
                suggestion="支持 wav / mp3 / m4a / flac / mp4 / mkv / mov"))
        max_bytes = self._app_cfg.upload_max_mb * 1024 * 1024
        if size_bytes > max_bytes:
            raise LecturelyError(ErrorInfo(
                "upload", "file_too_large",
                f"文件过大（{size_bytes // 1024 // 1024}MB > {self._app_cfg.upload_max_mb}MB）",
                suggestion=f"请上传不超过 {self._app_cfg.upload_max_mb}MB 的文件"))

    async def submit(self, *, file_path: str, file_name: str, source_language: str,
                     target_language: str, folder_id: Optional[str] = None,
                     title: Optional[str] = None) -> UploadTask:
        session = await SessionRepository().create(
            title=title or f"{Path(file_name).stem}",
            source_language=source_language, target_language=target_language,
            input_mode="file", folder_id=folder_id, status="processing",
        )
        task = UploadTask(new_id(), session["id"], file_name)
        self._tasks[task.task_id] = task
        asyncio.create_task(self._run(task, file_path, source_language, target_language))
        return task

    async def _run(self, task: UploadTask, file_path: str, source_language: str, target_language: str) -> None:
        async with self._lock:
            session_id = task.session_id
            source = FileSource(file_path, realtime=False)
            asr = WhisperLiveKitASR(self._app_cfg)
            translation = None
            try:
                await source.start()
                await asr.start_session(SessionASRConfig(
                    language=source_language, model=self._app_cfg.asr_model,
                    device=self._app_cfg.asr_device, compute_type=self._app_cfg.asr_compute_type,
                ))
                transcript = TranscriptManager(
                    session_id=session_id, source_language=source_language,
                    target_language=target_language, broadcast=self._broadcast,
                )
                # 翻译（与实时一致的启用条件）
                from .session_manager import _translation_enabled
                if _translation_enabled(source_language, target_language):
                    provider_name, provider_cfg = await get_provider_and_config("translation")
                    translation = build_translation_manager(
                        provider_name=provider_name, provider_config=provider_cfg,
                        target_language=target_language, source_language=source_language,
                        session_id=session_id, broadcast=self._broadcast,
                    )
                    translation.start()

                async def feed():
                    async for frame in source.frames():
                        await asr.push_audio(frame)
                        if source.progress() is not None:
                            task.progress = source.progress()

                async def consume():
                    async for event in asr.events():
                        if isinstance(event, CommittedSentence):
                            seg = await transcript.handle_committed(event)
                            if seg is not None:
                                task.segment_count = transcript.segment_count
                                if translation is not None:
                                    await translation.submit(seg)

                consumer = asyncio.create_task(consume())
                await feed()
                await asr.flush()
                await consumer
                await asr.close()

                if translation is not None:
                    await translation.drain(timeout=30)
                    await translation.stop()

                duration_ms = source.duration_ms or int(source._emitted / 16000 * 1000)
                await SessionRepository().update_status(
                    session_id, "completed", duration_ms=duration_ms,
                    segment_count=task.segment_count, ended_at=None,
                )
                task.status = "completed"
                task.progress = 1.0
                logger.info("文件转录完成：%s（%d 句）", task.file_name, task.segment_count)
            except Exception as e:  # noqa: BLE001
                logger.error("文件转录失败 %s：%s", task.file_name, e)
                task.status = "failed"
                task.error = str(e)
                await SessionRepository().update_status(session_id, "failed")
            finally:
                try:
                    await source.stop()
                except Exception:
                    pass
                try:
                    if translation is not None:
                        await translation.stop()
                except Exception:
                    pass


async def _null_broadcast(event: dict) -> None:
    pass


_upload_manager: Optional[UploadManager] = None


def get_upload_manager(app_cfg: AppConfig, broadcast=None) -> UploadManager:
    global _upload_manager
    if _upload_manager is None:
        _upload_manager = UploadManager(app_cfg, broadcast)
    return _upload_manager
