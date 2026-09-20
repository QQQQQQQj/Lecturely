"""SessionManager：实时会话生命周期编排（对应 docs/architecture/06）。

职责：AudioSource → ASR → TranscriptManager 的任务编排；暂停/继续/停止；单活动会话约束。
时间基准：ASR 提交 token 的时间戳 = 已推入 ASR 的音频时长（暂停不计入），与录音对齐。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..asr.base import ASREventKind, CommittedSentence, EngineStatusEvent, PartialEvent, SessionASRConfig
from ..asr.wlk_engine import WhisperLiveKitASR
from ..audio.base import AudioFrame, InputMode
from ..audio.mic_source import MicSource
from ..config import AppConfig
from ..errors import ErrorInfo, LecturelyError
from ..logging_setup import get_logger
from ..storage.repositories.sessions import SessionRepository
from ..translation.base import normalize_lang
from ..utils import new_id, now_ms
from ..websocket import events as ev
from .app_settings import get_provider_and_config
from .recording_manager import RecordingManager
from .translation_manager import TranslationManager, build_translation_manager
from .transcript_manager import TranscriptManager

logger = get_logger(__name__)

Broadcast = Callable[[dict], Awaitable[None]]


@dataclass
class LiveSessionConfig:
    input_mode: str = "mic"
    mic_device: str = "default"
    system_device: str = "default"
    source_language: str = "auto"
    target_language: str = "zh-CN"
    save_recording: bool = True
    folder_id: Optional[str] = None
    title: Optional[str] = None


@dataclass
class ActiveSession:
    session_id: str
    cfg: LiveSessionConfig
    source: object
    asr: WhisperLiveKitASR
    transcript: TranscriptManager
    status: str = "recording"
    started_at: int = field(default_factory=now_ms)
    pushed_samples: int = 0
    audio_task: Optional[asyncio.Task] = None
    event_task: Optional[asyncio.Task] = None
    translation: Optional[TranslationManager] = None
    recording: Optional[RecordingManager] = None
    stop_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionManager:
    def __init__(self, app_cfg: AppConfig, broadcast: Broadcast):
        self._app_cfg = app_cfg
        self._broadcast = broadcast
        self.active: Optional[ActiveSession] = None
        self._session_repo = SessionRepository()
        # 翻译/录音管理器由后续里程碑注入（可选）
        self.on_committed: Optional[Callable[[dict], Awaitable[None]]] = None
        self.on_audio_frame: Optional[Callable[[AudioFrame], Awaitable[None]]] = None

    def current_status(self) -> dict:
        if self.active is None:
            return {"active": False, "status": "idle"}
        return {
            "active": True, "status": self.active.status,
            "session_id": self.active.session_id,
            "segment_count": self.active.transcript.segment_count,
        }

    async def start(self, cfg: LiveSessionConfig) -> dict:
        if self.active is not None and self.active.status in ("recording", "paused", "processing"):
            raise LecturelyError(ErrorInfo(
                scope="session", code="session_already_active",
                message="已有一个进行中的实时会话",
                suggestion="请先停止当前会话，再开始新的转录", retryable=False))

        # 1. 音频源（M4：麦克风）
        source = self._build_source(cfg)
        await source.start()  # 失败抛 AudioSourceError（不静默降级）

        # 2. 会话记录
        title = cfg.title or _default_title()
        sess = await self._session_repo.create(
            title=title, source_language=cfg.source_language, target_language=cfg.target_language,
            input_mode=cfg.input_mode, folder_id=cfg.folder_id, status="recording",
        )
        session_id = sess["id"]

        # 3. ASR
        asr = WhisperLiveKitASR(self._app_cfg)
        try:
            await asr.start_session(SessionASRConfig(
                language=cfg.source_language, model=self._app_cfg.asr_model,
                device=self._app_cfg.asr_device, compute_type=self._app_cfg.asr_compute_type,
            ))
        except Exception as e:
            await source.stop()
            await self._session_repo.update_status(session_id, "failed")
            raise LecturelyError(ErrorInfo(
                scope="asr", code="asr_start_failed",
                message=f"ASR 启动失败：{e}",
                suggestion="请检查模型是否已下载（设置页），或查看诊断页", retryable=True)) from e

        transcript = TranscriptManager(
            session_id=session_id, source_language=cfg.source_language,
            target_language=cfg.target_language, broadcast=self._broadcast,
        )
        active = ActiveSession(session_id=session_id, cfg=cfg, source=source,
                               asr=asr, transcript=transcript, status="recording")
        self.active = active

        # 4. 实时翻译（源语言 ≠ 目标语言时启用；失败不阻塞原文）
        if _translation_enabled(cfg.source_language, cfg.target_language):
            try:
                provider_name, provider_cfg = await get_provider_and_config("translation")
                tm = build_translation_manager(
                    provider_name=provider_name, provider_config=provider_cfg,
                    target_language=cfg.target_language, source_language=cfg.source_language,
                    session_id=session_id, broadcast=self._broadcast,
                )
                tm.start()
                active.translation = tm
                async def _on_committed(seg: dict, _tm=tm) -> None:
                    await _tm.submit(seg)
                self.on_committed = _on_committed
                logger.info("实时翻译已启用：provider=%s → %s", provider_name, cfg.target_language)
            except Exception as e:  # noqa: BLE001
                logger.warning("翻译初始化失败（不影响转录）：%s", e)
                await self._broadcast(ev.error_event(
                    scope="translation", code="translation_init_failed", session_id=session_id,
                    message=f"翻译服务初始化失败：{e}",
                    suggestion="请在设置页检查翻译服务配置；原文转录不受影响", retryable=True))

        # 5. 自动录音（可选开启；写盘失败不影响转录）
        if cfg.save_recording:
            rm = RecordingManager(session_id=session_id,
                                  recordings_dir=self._app_cfg.recordings_dir,
                                  broadcast=self._broadcast)
            rm.start()
            active.recording = rm
            async def _on_frame(frame: AudioFrame, _rm=rm) -> None:
                _rm.write(frame)
            self.on_audio_frame = _on_frame

        # 6. 启动音频泵与事件泵
        active.audio_task = asyncio.create_task(self._audio_pump(active))
        active.event_task = asyncio.create_task(self._event_pump(active))

        await self._broadcast(ev.make_event(ev.SESSION_STARTED, session_id=session_id, payload={
            "session_id": session_id, "title": title, "input_mode": cfg.input_mode,
            "source_language": cfg.source_language, "target_language": cfg.target_language,
            "started_at": active.started_at,
        }))
        logger.info("实时会话开始：%s（%s，%s→%s）", session_id, cfg.input_mode,
                    cfg.source_language, cfg.target_language)
        return sess

    def _build_source(self, cfg: LiveSessionConfig):
        from ..audio import device_manager as dm
        from ..audio.mixed_source import MixedSource
        from ..audio.system_source import SystemSource

        # 严格解析（根因 2）："default" 解析为明确 raw endpoint（虚拟默认时优先物理 WASAPI）；
        # 陈旧 ID 直接抛 device_not_found，禁止静默回退。
        if cfg.input_mode == InputMode.MIC.value:
            mic_raw, mic_name = dm.resolve_input_strict(cfg.mic_device)
            logger.info("麦克风会话使用：%s (raw=%s)", mic_name, mic_raw)
            return MicSource(device=mic_raw, name=mic_name)
        if cfg.input_mode == InputMode.SYSTEM.value:
            _, sys_name, sys_raw = dm.resolve_output_strict(cfg.system_device)
            logger.info("系统音频会话使用：%s (raw=%s)", sys_name, sys_raw)
            return SystemSource(raw_output_index=sys_raw, name=sys_name)
        if cfg.input_mode == InputMode.MIXED.value:
            mic_raw, _ = dm.resolve_input_strict(cfg.mic_device)
            _, _, sys_raw = dm.resolve_output_strict(cfg.system_device)
            return MixedSource(mic_device=mic_raw, system_raw_index=sys_raw)
        raise LecturelyError(ErrorInfo(
            scope="audio", code="input_mode_unsupported",
            message=f"输入模式 {cfg.input_mode} 暂未实现",
            suggestion="文件上传转录将在后续版本提供", retryable=False))

    async def _audio_pump(self, active: ActiveSession) -> None:
        frames_since_level = 0
        try:
            async for frame in active.source.frames():
                active.pushed_samples += len(frame.samples)
                if self.on_audio_frame is not None:
                    await self.on_audio_frame(frame)
                await active.asr.push_audio(frame)
                # 电平上报（约每帧一次，驱动 UI 电平表）
                frames_since_level += 1
                if frames_since_level >= 1:
                    frames_since_level = 0
                    await self._report_levels(active)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("音频泵异常：%s", e)
            await self._broadcast(ev.error_event(
                scope="audio", code="audio_pump_error", session_id=active.session_id,
                message=f"音频采集中断：{e}", suggestion="请检查麦克风连接后重试", retryable=True))

    async def _report_levels(self, active: ActiveSession) -> None:
        src = active.source
        mic_lvl = 0.0
        sys_lvl = 0.0
        try:
            if hasattr(src, "mic_level") and hasattr(src, "system_level"):
                mic_lvl = src.mic_level()  # type: ignore[attr-defined]
                sys_lvl = src.system_level()  # type: ignore[attr-defined]
            else:
                lvl = src.level()  # type: ignore[attr-defined]
                if active.cfg.input_mode == "system":
                    sys_lvl = lvl
                else:
                    mic_lvl = lvl
        except Exception:
            pass
        await self._broadcast(ev.make_event(ev.AUDIO_STATUS, session_id=active.session_id, payload={
            "state": "running", "mic_level": round(mic_lvl, 3), "system_level": round(sys_lvl, 3),
        }))

    async def _event_pump(self, active: ActiveSession) -> None:
        try:
            async for event in active.asr.events():
                if isinstance(event, PartialEvent):
                    await active.transcript.handle_partial(event.text, event.start_ms)
                elif isinstance(event, CommittedSentence):
                    seg = await active.transcript.handle_committed(event)
                    if seg is not None and self.on_committed is not None:
                        await self.on_committed(seg)
                elif isinstance(event, EngineStatusEvent):
                    if event.kind == ASREventKind.ENGINE_ERROR:
                        await self._broadcast(ev.error_event(
                            scope="asr", code="asr_runtime_error", session_id=active.session_id,
                            message=f"ASR 运行错误：{event.message}", retryable=True))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("事件泵异常：%s", e)

    async def pause(self) -> None:
        active = self._require_active()
        if active.status != "recording":
            return
        active.status = "paused"
        await active.source.stop()
        if active.audio_task is not None:
            active.audio_task.cancel()
            active.audio_task = None
        await self._session_repo.update_status(active.session_id, "paused")
        await self._broadcast(ev.make_event(ev.SESSION_PAUSED, session_id=active.session_id))
        logger.info("会话暂停：%s", active.session_id)

    async def resume(self) -> None:
        active = self._require_active()
        if active.status != "paused":
            return
        await active.source.start()
        active.audio_task = asyncio.create_task(self._audio_pump(active))
        active.status = "recording"
        await self._session_repo.update_status(active.session_id, "recording")
        await self._broadcast(ev.make_event(ev.SESSION_RESUMED, session_id=active.session_id))
        logger.info("会话继续：%s", active.session_id)

    async def stop(self) -> Optional[dict]:
        active = self._require_active()
        async with active.stop_lock:  # 防重入
            if active.status in ("processing", "completed"):
                return None
            active.status = "processing"
            # 立即广播“收尾中”：前端秒级反馈（停止采集、计时器停），
            # 避免收尾期间 UI 看起来“点了停止没反应、还在录制”。
            await self._broadcast(ev.make_event(ev.SESSION_PROCESSING, session_id=active.session_id))
            await self._session_repo.update_status(active.session_id, "processing")

            # 1. 停止采集
            await active.source.stop()
            if active.audio_task is not None:
                active.audio_task.cancel()
                active.audio_task = None

            # 2. ASR flush 尾部并等待事件泵排空（短超时：尾并不值得让用户等待十几秒）
            await active.asr.flush()
            if active.event_task is not None:
                try:
                    await asyncio.wait_for(active.event_task, timeout=6)
                except asyncio.TimeoutError:
                    logger.warning("事件泵排空超时，强制收尾")
                    active.event_task.cancel()
                active.event_task = None
            await active.asr.close()

            # 2.5 等待在途翻译完成（短超时，超时后标记失败不再等待）
            if active.translation is not None:
                await active.translation.drain(timeout=6)
                await active.translation.stop()
                active.translation = None
            self.on_committed = None

            # 2.6 录音 finalize
            if active.recording is not None:
                await active.recording.finalize()
                active.recording = None
            self.on_audio_frame = None

            # 3. 收尾
            duration_ms = int(active.pushed_samples / 16000 * 1000)
            ended = now_ms()
            await self._session_repo.update_status(
                active.session_id, "completed",
                ended_at=ended, duration_ms=duration_ms,
                segment_count=active.transcript.segment_count,
            )
            await self._broadcast(ev.make_event(ev.SESSION_COMPLETED, session_id=active.session_id, payload={
                "session_id": active.session_id, "duration_ms": duration_ms,
                "segment_count": active.transcript.segment_count,
            }))
            logger.info("会话完成：%s（%d 句，%dms）", active.session_id,
                        active.transcript.segment_count, duration_ms)
            result = {"session_id": active.session_id, "duration_ms": duration_ms,
                      "segment_count": active.transcript.segment_count}
            self.active = None
            return result

    async def retry_translation(self, segment_id: str) -> None:
        active = self.active
        if active is not None and active.translation is not None:
            await active.translation.retry(segment_id)

    async def shutdown(self) -> None:
        """应用关闭时：尽量收尾活动会话。"""
        if self.active is not None and self.active.status in ("recording", "paused"):
            try:
                await self.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("关闭时收尾会话失败：%s", e)

    def _require_active(self) -> ActiveSession:
        if self.active is None:
            raise LecturelyError(ErrorInfo(
                scope="session", code="no_active_session",
                message="当前没有进行中的会话", suggestion="请先在首页开始转录"))
        return self.active


def _default_title() -> str:
    import datetime
    return "会话 " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _translation_enabled(source_language: str, target_language: str) -> bool:
    """源语言与目标语言不同时启用翻译；auto 源语言总是启用。"""
    if not target_language:
        return False
    if source_language in ("auto", "", None):
        return True
    return normalize_lang(source_language) != normalize_lang(target_language)
