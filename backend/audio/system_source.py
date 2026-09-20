"""系统音频采集源（Windows WASAPI Loopback，PyAudioWPatch）。

根因 6：loopback 选择不再用名称子串匹配 + 任意回退，而是：
- 默认输出 → 官方 `get_default_wasapi_loopback()`；
- 指定输出 → `get_wasapi_loopback_analogue_by_index(raw_index)`；
- 找不到对应设备时明确失败（loopback_not_found），禁止任意 fallback。

根因 7：生命周期安全：
- 每次 start 使用独立 stop_event 和 generation；旧代回调数据一律丢弃；
- 只有开流成功后才进入 running；失败回滚（关流、terminate PyAudio、清线程/状态）；
- stop 中止并 join read/watch 线程；
- restart 失败重试，超过次数向 frames() 抛 AudioSourceError（不永久静默）。

根因 5：读取线程经 `loop.call_soon_threadsafe()` 写 event-loop 的 asyncio.Queue
（有界，满时丢最旧）；不再把阻塞 queue.get 提交给 executor。
"""
from __future__ import annotations

import asyncio
import platform
import threading
from typing import AsyncIterator, Optional

import numpy as np

from ..errors import AudioSourceError
from ..logging_setup import get_logger
from .base import AudioFrame, DeviceInfo, InputMode, SAMPLE_RATE
from .resample import StreamResampler, to_mono

logger = get_logger(__name__)

CHUNK_MS = 500
DEVICE_CHECK_INTERVAL = 2.0
QUEUE_MAX = 64
RESTART_MAX_RETRIES = 3

_STOP = object()


def _is_windows() -> bool:
    return platform.system() == "Windows"


class SystemSource:
    def __init__(self, device: Optional[str] = None, name: Optional[str] = None,
                 raw_output_index: Optional[int] = None):
        self.mode = InputMode.SYSTEM
        # 根因 6：全程保留 WASAPI output raw index（None = 默认输出）
        self._raw_output_index = raw_output_index
        self._device_name = None if device in (None, "default") else device
        self.name = name or "系统音频"
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._pa = None
        self._pyaudio = None
        self._stream = None
        self._resampler: Optional[StreamResampler] = None
        self._native_rate = 44100
        self._native_channels = 2
        self._native_chunk = 4410
        # 根因 7：generation + 独立 stop event
        self._generation = 0
        self._stop_event = threading.Event()
        self._read_thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._restart = threading.Event()
        self._running = False
        self._current_device_name: Optional[str] = None
        self._emitted_samples = 0
        self._pending = np.empty(0, dtype=np.float32)
        self._level = 0.0
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._fatal_error: Optional[AudioSourceError] = None

    # ---------- 入队（读取线程 → event loop） ----------

    def _enqueue_threadsafe(self, generation: int, block) -> None:
        # 旧代（上一会话/重启前）的音频一律丢弃（根因 7：不得写入新会话）
        if not self._running or generation != self._generation:
            return
        try:
            self._queue.put_nowait(block)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(block)
            except Exception:
                pass

    # ---------- 启动 ----------

    async def start(self) -> None:
        if not _is_windows():
            raise AudioSourceError("platform_unsupported",
                                   "当前平台暂不支持系统音频捕获",
                                   "系统音频（WASAPI Loopback）目前仅支持 Windows；请改用麦克风模式")
        try:
            import pyaudiowpatch as pyaudio
        except Exception as e:
            raise AudioSourceError("pyaudiowpatch_missing", "未安装 pyaudiowpatch",
                                   "请重新运行安装脚本") from e
        self._pyaudio = pyaudio
        self._loop = asyncio.get_running_loop()
        self._generation += 1
        self._stop_event = threading.Event()
        self._fatal_error = None
        self._pa = pyaudio.PyAudio()
        try:
            self._open_stream()
        except Exception:
            # 根因 7：start 失败必须完整回滚
            self._rollback_start()
            raise
        # 只有开流成功后才进入 running
        self._running = True
        self._emitted_samples = 0
        gen = self._generation
        self._read_thread = threading.Thread(
            target=self._read_loop, args=(gen, self._stop_event), daemon=True)
        self._read_thread.start()
        if self._raw_output_index is None and self._device_name is None:
            self._watch_thread = threading.Thread(
                target=self._watch_default_device, args=(self._stop_event,), daemon=True)
            self._watch_thread.start()
        logger.info("系统音频采集已启动：%s (gen=%d)", self._current_device_name, gen)

    def _rollback_start(self) -> None:
        self._running = False
        self._close_stream()
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        self._read_thread = None
        self._watch_thread = None

    # ---------- loopback 精确选择（根因 6） ----------

    def _find_loopback_device(self) -> dict:
        pa = self._pa
        if self._raw_output_index is not None:
            # 指定输出：官方 analogue helper，找不到明确失败，禁止任意 fallback
            try:
                dev = pa.get_wasapi_loopback_analogue_by_index(self._raw_output_index)
            except Exception as e:
                raise AudioSourceError(
                    "loopback_not_found",
                    f"未找到输出设备 raw={self._raw_output_index} 对应的回环设备",
                    "该输出设备可能已移除，请刷新设备列表重新选择") from e
            if dev is None:
                raise AudioSourceError(
                    "loopback_not_found",
                    f"输出设备 raw={self._raw_output_index} 没有对应的 loopback 端点",
                    "请刷新设备列表重新选择输出设备")
            return dev
        # 默认输出：官方 default helper
        try:
            return pa.get_default_wasapi_loopback()
        except Exception as e:
            raise AudioSourceError(
                "loopback_not_found", "未找到默认输出对应的回环（loopback）设备",
                "请检查音频输出设备是否正常，或尝试更换默认输出设备") from e

    def _open_stream(self) -> None:
        loopback_dev = self._find_loopback_device()
        self._native_channels = int(loopback_dev["maxInputChannels"])
        self._native_rate = int(loopback_dev["defaultSampleRate"])
        self._current_device_name = loopback_dev["name"]
        self.name = loopback_dev["name"]
        self._resampler = StreamResampler(self._native_rate, SAMPLE_RATE)
        native_chunk = int(self._native_rate * 0.1)  # 100ms
        stream = None
        try:
            # 阻塞读取模式（非 callback）：loopback + callback 在部分环境不稳定
            stream = self._pa.open(
                format=self._pyaudio.paFloat32,
                channels=self._native_channels,
                rate=self._native_rate,
                input=True,
                input_device_index=loopback_dev["index"],
                frames_per_buffer=native_chunk,
            )
            stream.start_stream()
        except AudioSourceError:
            raise
        except Exception as e:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise AudioSourceError("loopback_open_failed", f"系统音频流打开失败：{e}",
                                   "可能音频设备被占用或驱动异常，请重试") from e
        self._stream = stream
        self._native_chunk = native_chunk

    # ---------- 读取线程 ----------

    def _restart_stream(self) -> None:
        """在读取线程内重启采集流（默认设备变化后调用）。"""
        self._close_stream()
        try:
            self._pa.terminate()
        except Exception:
            pass
        self._pa = self._pyaudio.PyAudio()
        self._open_stream()
        logger.info("采集已重启于：%s", self._current_device_name)

    def _read_loop(self, generation: int, stop_event: threading.Event) -> None:
        """后台线程：阻塞读取 loopback → mono → soxr 重采样 → 入队（带 generation）。"""
        import time
        restart_failures = 0
        while not stop_event.is_set():
            if self._restart.is_set():
                self._restart.clear()
                try:
                    self._restart_stream()
                    restart_failures = 0
                except Exception as e:  # noqa: BLE001
                    restart_failures += 1
                    logger.error("重启采集失败（%d/%d）：%s",
                                 restart_failures, RESTART_MAX_RETRIES, e)
                    if restart_failures >= RESTART_MAX_RETRIES:
                        # 根因 7：不永久静默——向 frames() 抛结构化错误
                        self._fatal_error = AudioSourceError(
                            "loopback_restart_failed",
                            f"系统音频采集重启失败（已重试 {restart_failures} 次）：{e}",
                            "请检查默认输出设备，或停止会话后重新开始")
                        if self._loop is not None:
                            self._loop.call_soon_threadsafe(
                                self._enqueue_threadsafe, generation, _STOP)
                        return
                    self._restart.set()  # 继续重试
                    time.sleep(0.5)
                continue
            stream = self._stream
            if stream is None:
                time.sleep(0.01)
                continue
            try:
                if stream.get_read_available() >= self._native_chunk:
                    data = stream.read(self._native_chunk, exception_on_overflow=False)
                    mono = to_mono(np.frombuffer(data, dtype=np.float32), self._native_channels)
                    resampled = self._resampler.process(mono) if self._resampler else mono
                    if resampled.size and self._loop is not None:
                        self._level = float(np.sqrt(np.mean(resampled ** 2))) * 3.0
                        self._loop.call_soon_threadsafe(
                            self._enqueue_threadsafe, generation, resampled)
                else:
                    time.sleep(0.01)
            except Exception as e:  # noqa: BLE001
                logger.warning("loopback 读取异常（设备可能已变化）：%s", e)
                time.sleep(0.3)

    def _close_stream(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def _query_current_default(self) -> Optional[str]:
        try:
            pa = self._pyaudio.PyAudio()
            try:
                for i in range(pa.get_host_api_count()):
                    info = pa.get_host_api_info_by_index(i)
                    if "WASAPI" in info["name"]:
                        dev = pa.get_device_info_by_index(info["defaultOutputDevice"])
                        return dev["name"]
            finally:
                pa.terminate()
        except Exception:
            return None
        return None

    def _watch_default_device(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if stop_event.wait(DEVICE_CHECK_INTERVAL):
                break
            try:
                current = self._query_current_default()
                if (current and self._current_device_name
                        and current not in self._current_device_name):
                    logger.info("默认输出设备变化：%s → %s，重启采集",
                                self._current_device_name, current)
                    self._restart.set()
            except Exception as e:  # noqa: BLE001
                logger.debug("默认设备跟踪异常：%s", e)

    # ---------- 帧输出 ----------

    async def frames(self) -> AsyncIterator[AudioFrame]:
        chunk_len = int(SAMPLE_RATE * CHUNK_MS / 1000)
        while self._running:
            try:
                block = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if self._fatal_error is not None:
                    raise self._fatal_error
                continue
            if block is _STOP:
                if self._fatal_error is not None:
                    raise self._fatal_error
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
        self._stop_event.set()
        self._close_stream()
        # 根因 7：join read/watch 线程，防 pause/resume 产生重复线程
        for t in (self._read_thread, self._watch_thread):
            if t is not None and t.is_alive():
                await asyncio.get_running_loop().run_in_executor(None, t.join, 3.0)
        self._read_thread = None
        self._watch_thread = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        try:
            self._queue.put_nowait(_STOP)
        except Exception:
            pass

    def level(self) -> float:
        return min(self._level, 1.0)


def enumerate_output_devices() -> list[DeviceInfo]:
    """枚举系统音频输出设备（用于选择 loopback 源）。"""
    if not _is_windows():
        return []
    try:
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        try:
            wasapi_idx = None
            default_name = None
            for i in range(pa.get_host_api_count()):
                info = pa.get_host_api_info_by_index(i)
                if "WASAPI" in info["name"]:
                    wasapi_idx = info["index"]
                    try:
                        default_name = pa.get_device_info_by_index(info["defaultOutputDevice"])["name"]
                    except Exception:
                        pass
                    break
            if wasapi_idx is None:
                return []
            result = []
            seen = set()
            for i in range(pa.get_device_count()):
                dev = pa.get_device_info_by_index(i)
                if (dev["hostApi"] == wasapi_idx and dev["maxOutputChannels"] > 0
                        and not dev.get("isLoopbackDevice", False)):
                    name = dev["name"]
                    if name in seen:
                        continue
                    seen.add(name)
                    result.append(DeviceInfo(
                        id=name, name=name, kind="output",
                        is_default=(name == default_name),
                        extra={"channels": int(dev["maxOutputChannels"]),
                               "samplerate": int(dev["defaultSampleRate"]),
                               "raw_index": i},
                    ))
            return result
        finally:
            pa.terminate()
    except Exception as e:  # noqa: BLE001
        logger.warning("枚举输出设备失败：%s", e)
        return []
