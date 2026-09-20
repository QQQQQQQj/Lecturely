"""WhisperLiveKit ASR 引擎适配（主底座，pip 依赖嵌入）。

集成要点（M2 已验证）：
- 进程级共享 TranscriptionEngine（faster-whisper + LocalAgreement，pcm_input=True）。
- 每会话 AudioProcessor + create_tasks() 独立启动后台转录处理器。
- 通过轮询 proc.state.tokens（追加态、词级、含 start/end/probability）获取新增 committed token，
  交 SentenceCommitter 聚成稳定非重叠句子；proc.state.buffer_transcription 作为 partial（仅 UI）。
- 不持久化 FrontData.lines（其为累积展示态）。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from ..audio.base import AudioFrame
from ..config import AppConfig
from ..logging_setup import get_logger
from .base import (
    ASRCapabilities, ASREventKind, CommittedSentence, EngineStatusEvent,
    PartialEvent, SessionASRConfig,
)
from .committer import SentenceCommitter

logger = get_logger(__name__)

# ---- 进程级共享引擎（单例，延迟初始化）----
_shared_engine = None
_engine_lock = asyncio.Lock()
_engine_ready = asyncio.Event()
_engine_error: Optional[str] = None


async def preload_engine(app_cfg: AppConfig) -> None:
    """后台预加载 ASR 引擎（模型下载 + 加载 + warmup），不阻塞服务启动。"""
    global _shared_engine, _engine_error
    async with _engine_lock:
        if _shared_engine is not None or _engine_error is not None:
            return
        try:
            from whisperlivekit import TranscriptionEngine
            from .runtime import resolve_asr_runtime
            # 根因 8：先解析安全的 device/compute_type（CUDA 运行库不完整时回退 CPU/int8），
            # 经环境变量透传给 whisperlivekit → faster-whisper WhisperModel。
            rt = resolve_asr_runtime(app_cfg.asr_device, app_cfg.asr_compute_type)
            logger.info("加载 ASR 引擎：model=%s backend=%s policy=%s device=%s compute=%s",
                        app_cfg.asr_model, app_cfg.asr_backend, app_cfg.asr_policy,
                        rt.device, rt.compute_type)
            loop = asyncio.get_running_loop()

            def _build():
                return TranscriptionEngine(
                    model_size=app_cfg.asr_model,  # WhisperLiveKitConfig 字段为 model_size
                    backend=app_cfg.asr_backend,
                    backend_policy=app_cfg.asr_policy,
                    pcm_input=True,
                )

            _shared_engine = await loop.run_in_executor(None, _build)
            _engine_ready.set()
            logger.info("ASR 引擎就绪")
        except Exception as e:  # noqa: BLE001
            _engine_error = str(e)
            _engine_ready.set()  # 解除等待，让调用方读到错误
            logger.error("ASR 引擎加载失败：%s", e)


async def _get_engine(app_cfg: AppConfig):
    if _shared_engine is None and _engine_error is None:
        await preload_engine(app_cfg)
    if _engine_error is not None:
        raise RuntimeError(f"ASR 引擎不可用：{_engine_error}")
    return _shared_engine


def engine_status() -> dict:
    if _shared_engine is not None:
        return {"state": "ready", "message": "ASR 引擎就绪"}
    if _engine_error is not None:
        return {"state": "error", "message": _engine_error}
    return {"state": "loading", "message": "ASR 引擎加载中"}


class WhisperLiveKitASR:
    """WhisperLiveKit 流式 ASR Provider。"""

    def __init__(self, app_cfg: AppConfig):
        self._app_cfg = app_cfg
        self._proc = None
        self._committer: Optional[SentenceCommitter] = None
        self._out: asyncio.Queue = asyncio.Queue()
        self._poll_task: Optional[asyncio.Task] = None
        self._consumed = 0
        self._accept_audio = False
        self._flushed = False
        self._language = "auto"

    async def start_session(self, cfg: SessionASRConfig) -> None:
        from whisperlivekit import AudioProcessor
        engine = await _get_engine(self._app_cfg)
        self._language = cfg.language
        self._committer = SentenceCommitter()
        language = None if cfg.language in ("auto", "", None) else cfg.language
        self._proc = AudioProcessor(transcription_engine=engine, language=language)
        await self._proc.create_tasks()  # 后台处理器独立运行；结果生成器忽略
        self._consumed = 0
        self._flushed = False
        self._accept_audio = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        await self._out.put(EngineStatusEvent(kind=ASREventKind.ENGINE_READY, message="ASR 会话已开始"))
        logger.info("ASR 会话开始：language=%s", cfg.language)

    async def push_audio(self, frame: AudioFrame) -> None:
        if self._proc is not None and self._accept_audio and not self._flushed:
            try:
                await self._proc.process_audio(frame.to_int16_bytes())
            except Exception as e:  # noqa: BLE001
                logger.warning("push_audio 失败：%s", e)

    async def _poll_loop(self) -> None:
        last_partial = ""
        try:
            while True:
                # 读取新增 committed tokens 与 partial（state 受 proc.lock 保护）
                tokens, partial_text, done = await self._snapshot()
                for tok in tokens:
                    sentence = self._committer.add_token(
                        text=tok.get("text", ""), start_s=tok.get("start"), end_s=tok.get("end"),
                        probability=tok.get("probability"), speaker=tok.get("speaker"),
                        detected_language=tok.get("detected_language"),
                    )
                    if sentence is not None:
                        await self._out.put(sentence)
                if partial_text and partial_text != last_partial:
                    last_partial = partial_text
                    await self._out.put(PartialEvent(text=partial_text))
                if done:
                    remainder = self._committer.flush() if self._committer else None
                    if remainder is not None:
                        await self._out.put(remainder)
                    break
                await asyncio.sleep(0.04)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("ASR 轮询异常：%s", e)
            await self._out.put(EngineStatusEvent(kind=ASREventKind.ENGINE_ERROR, message=str(e)))
        finally:
            await self._out.put(None)  # 事件流结束哨兵

    async def _snapshot(self) -> tuple[list[dict], str, bool]:
        """在锁内拷贝新增 tokens 与 partial；返回 (新tokens, partial, 是否处理完成)。

        根因 9：同时检测 AudioProcessor 保存的致命转写错误（_transcription_error），
        发现后抛出以便转为 ENGINE_ERROR 事件上报前端。
        """
        proc = self._proc
        if proc is None:
            return [], "", True
        err = getattr(proc, "_transcription_error", None)
        if err is not None:
            raise RuntimeError(f"转写引擎致命错误：{err}")
        async with proc.lock:
            all_tokens = proc.state.tokens
            new_tokens = [
                {
                    "text": t.text, "start": t.start, "end": t.end,
                    "probability": t.probability,
                    "speaker": (str(t.speaker) if getattr(t, "speaker", None) is not None else None),
                    "detected_language": getattr(t, "detected_language", None),
                }
                for t in all_tokens[self._consumed:]
            ]
            self._consumed = len(all_tokens)
            buf = proc.state.buffer_transcription
            partial = buf.text if buf else ""
        done = False
        if self._flushed:
            task = getattr(proc, "transcription_task", None)
            done = bool(getattr(proc, "is_stopping", False)) and (task is None or task.done())
        return new_tokens, (partial or ""), done

    async def events(self) -> AsyncIterator:
        while True:
            item = await self._out.get()
            if item is None:
                break
            yield item

    async def flush(self) -> None:
        if self._proc is not None and not self._flushed:
            self._flushed = True
            self._accept_audio = False
            try:
                await self._proc.process_audio(b"")  # 结束信号，触发尾部 flush
            except Exception as e:  # noqa: BLE001
                logger.warning("ASR flush 失败：%s", e)

    async def close(self) -> None:
        self._accept_audio = False
        if self._poll_task is not None:
            try:
                # 短超时：收尾不应让用户长时间等待，超时直接取消轮询
                await asyncio.wait_for(self._poll_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._poll_task.cancel()
            self._poll_task = None
        self._proc = None

    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(streaming=True, word_timestamps=True, diarization=False)
