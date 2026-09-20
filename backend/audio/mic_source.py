"""麦克风采集源（sounddevice / PortAudio）。

以协商格式开流（与设备测试共用 mic_opener，根因 3）→ mono → soxr 重采样到 16kHz
→ 定长帧异步产出。

根因 5 修复：采集回调经 `loop.call_soon_threadsafe()` 写入 event-loop 所属的
`asyncio.Queue`（有界，满时丢最旧保实时）。禁止把永久阻塞的 `queue.get`
提交到 executor——那样 Future 超时取消后底层线程不会结束，会继续偷走
之后到达的音频块。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

import numpy as np

from ..errors import AudioSourceError
from ..logging_setup import get_logger
from .base import AudioFrame, DeviceInfo, InputMode, SAMPLE_RATE
from .mic_opener import NegotiatedFormat, negotiate_and_open_resilient, negotiate_input_format
from .resample import StreamResampler, to_mono

logger = get_logger(__name__)

CHUNK_MS = 500  # 每帧时长
QUEUE_MAX = 64  # 有界队列（~6.4 秒 100ms 块）

_STOP = object()  # 队列停止哨兵


class MicSource:
    def __init__(self, device: Optional[str] = None, name: Optional[str] = None):
        self.mode = InputMode.MIC
        # 严格解析后的 raw index（"default" 已在上游解析为明确端点，根因 2）
        self._device = None if device in (None, "default") else int(device)
        self.name = name or ("默认麦克风" if self._device is None else f"麦克风 {device}")
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._stream = None
        self._fmt: Optional[NegotiatedFormat] = None
        self._resampler: Optional[StreamResampler] = None
        self._running = False
        self._emitted_samples = 0
        self._pending = np.empty(0, dtype=np.float32)
        self._level = 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _enqueue_threadsafe(self, block: np.ndarray) -> None:
        """驱动线程 → event loop 队列；满时丢最旧保实时。"""
        if not self._running:
            return
        try:
            self._queue.put_nowait(block)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(block)
            except Exception:
                pass

    async def start(self) -> None:
        try:
            import sounddevice as sd  # noqa: F401
        except Exception as e:
            raise AudioSourceError("sounddevice_missing", "未安装 sounddevice",
                                   "请重新运行安装脚本") from e
        self._loop = asyncio.get_running_loop()
        loop = self._loop
        holder = {"channels": 1}

        def _callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                logger.debug("mic status: %s", status)
            if self._resampler is None:
                return  # 重采样器尚未就绪（start 还在收尾），丢弃前几个回调块
            try:
                mono = to_mono(indata, holder["channels"])
                resampled = self._resampler.process(mono) if self._resampler else mono
                if resampled.size:
                    self._level = float(np.sqrt(np.mean(resampled ** 2))) * 3.0
                    loop.call_soon_threadsafe(self._enqueue_threadsafe, resampled)
            except Exception as e:  # noqa: BLE001
                logger.warning("mic callback error: %s", e)

        self._running = True
        try:
            # 统一 opener（根因 3）+ 长驻进程设备快照过期时自动刷新重试
            self._stream, self._fmt = negotiate_and_open_resilient(
                self._device, _callback, blocksize_ms=100)
        except Exception:
            self._running = False
            raise
        holder["channels"] = self._fmt.channels
        self.name = self._fmt.name or self.name
        self._resampler = StreamResampler(self._fmt.sample_rate, SAMPLE_RATE)
        self._emitted_samples = 0
        logger.info("麦克风采集已启动：%s raw=%d @ %dHz ch=%d (%s)",
                    self.name, self._fmt.raw_id, self._fmt.sample_rate,
                    self._fmt.channels, self._fmt.host_api)

    async def frames(self) -> AsyncIterator[AudioFrame]:
        chunk_len = int(SAMPLE_RATE * CHUNK_MS / 1000)
        while self._running:
            try:
                block = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if block is _STOP:
                break
            self._pending = np.concatenate([self._pending, block])
            while self._pending.size >= chunk_len:
                frame_samples = self._pending[:chunk_len]
                self._pending = self._pending[chunk_len:]
                self._emitted_samples += chunk_len
                yield AudioFrame(
                    samples=frame_samples,
                    stream_time_ms=int(self._emitted_samples / SAMPLE_RATE * 1000),
                )

    async def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        # 哨兵解除 frames() 等待
        try:
            self._queue.put_nowait(_STOP)
        except Exception:
            pass

    def level(self) -> float:
        return min(self._level, 1.0)


def enumerate_input_devices() -> list[DeviceInfo]:
    """枚举输入设备（麦克风）。"""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_in = sd.default.device[0] if sd.default.device else None
        result = []
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                result.append(DeviceInfo(
                    id=str(i), name=d.get("name", f"设备 {i}"), kind="input",
                    is_default=(i == default_in),
                    extra={"samplerate": int(d.get("default_samplerate", SAMPLE_RATE)),
                           "channels": int(d.get("max_input_channels", 1))},
                ))
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("枚举输入设备失败：%s", e)
        return []
