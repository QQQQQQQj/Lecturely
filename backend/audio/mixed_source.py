"""混合采集源（麦克风 + 系统音频，双路混音）。

根因 4 修复：不再用 `asyncio.gather(anext(mic), anext(system))`——那会在系统
loopback 暂时无 packet 时把已有数据的麦克风一起卡死。改为：

- 麦克风与系统音频各自独立 pump 协程，把帧推入各自的有界实时队列
  （积压时丢最旧帧）；
- mixer 按固定 500ms 单调时钟（loop.time()）输出：每个周期各取最新一帧，
  缺少的一路补零；同一周期两路都到达也只生成一个混合帧（不双倍推进时间轴）；
- 单路失败：另一路继续，错误经 error_callback 反馈（SessionManager 转发
  WebSocket error 事件）；两路均失败：停止并抛出结构化 AudioSourceError。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable, Optional

import numpy as np

from ..errors import AudioSourceError
from ..logging_setup import get_logger
from .base import AudioFrame, InputMode, SAMPLE_RATE
from .mic_source import MicSource
from .system_source import SystemSource

logger = get_logger(__name__)

TICK_S = 0.5                      # 固定混音节拍：500ms
PUMP_QUEUE_MAX = 4                # 每路有界实时队列（积压丢最旧）
CHUNK_LEN = int(SAMPLE_RATE * TICK_S)


def soft_mix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """逐样本相加 + 软限幅。"""
    n = max(a.size, b.size)
    if a.size < n:
        a = np.concatenate([a, np.zeros(n - a.size, dtype=np.float32)])
    if b.size < n:
        b = np.concatenate([b, np.zeros(n - b.size, dtype=np.float32)])
    total = a + b
    over = np.abs(total) > 1.0
    if np.any(over):
        total[over] = total[over] / np.abs(total[over])
    return total.astype(np.float32)


class MixedSource:
    def __init__(self, mic_device: Optional[str] = None, system_device: Optional[str] = None,
                 system_raw_index: Optional[int] = None,
                 error_callback: Optional[Callable[[str, str], None]] = None):
        self.mode = InputMode.MIXED
        self._mic = MicSource(device=mic_device)
        self._system = SystemSource(device=system_device, raw_output_index=system_raw_index)
        self.name = f"混合（{self._mic.name} + {self._system.name}）"
        self._emitted = 0
        self._error_callback = error_callback
        self._mic_q: asyncio.Queue = asyncio.Queue(maxsize=PUMP_QUEUE_MAX)
        self._sys_q: asyncio.Queue = asyncio.Queue(maxsize=PUMP_QUEUE_MAX)
        self._mic_alive = False
        self._sys_alive = False
        self._pump_tasks: list[asyncio.Task] = []
        self._partial_error: Optional[str] = None

    async def start(self) -> None:
        errors = []
        try:
            await self._mic.start()
            self._mic_alive = True
        except AudioSourceError as e:
            errors.append(f"麦克风：{e.info.message}")
        try:
            await self._system.start()
            self._sys_alive = True
        except AudioSourceError as e:
            errors.append(f"系统音频：{e.info.message}")
        if not self._mic_alive and not self._sys_alive:
            raise AudioSourceError("mixed_start_failed",
                                   "混合模式两路均不可用：" + "；".join(errors),
                                   "请检查麦克风与系统音频设备")
        if errors:
            self._partial_error = "；".join(errors)
            logger.warning("混合模式部分路不可用：%s", self._partial_error)
            self._notify_error("mixed_partial", self._partial_error)
        else:
            self._partial_error = None

    def _notify_error(self, code: str, message: str) -> None:
        if self._error_callback is not None:
            try:
                self._error_callback(code, message)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _q_put_latest(q: asyncio.Queue, item: np.ndarray) -> None:
        """有界实时队列：满时丢最旧。"""
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(item)
            except Exception:
                pass

    async def _pump(self, source, q: asyncio.Queue, label: str) -> None:
        """独立 pump：把一路的帧持续推入其实时队列；失败时标记该路死亡并上报。"""
        try:
            async for frame in source.frames():
                self._q_put_latest(q, frame.samples)
        except AudioSourceError as e:
            logger.error("混合模式 %s 路失败：%s", label, e.info.message)
            self._notify_error(e.info.code, f"{label}：{e.info.message}")
        except Exception as e:  # noqa: BLE001
            logger.error("混合模式 %s 路异常：%s", label, e)
            self._notify_error("mixed_pump_error", f"{label}：{e}")
        finally:
            if label == "麦克风":
                self._mic_alive = False
            else:
                self._sys_alive = False

    @staticmethod
    def _drain_latest(q: asyncio.Queue) -> Optional[np.ndarray]:
        """取该路当前累积的所有帧拼接（保持时间连续）；无帧返回 None。"""
        blocks = []
        while True:
            try:
                blocks.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not blocks:
            return None
        return np.concatenate(blocks)

    async def frames(self) -> AsyncIterator[AudioFrame]:
        loop = asyncio.get_running_loop()
        if self._mic_alive:
            self._pump_tasks.append(asyncio.create_task(
                self._pump(self._mic, self._mic_q, "麦克风")))
        if self._sys_alive:
            self._pump_tasks.append(asyncio.create_task(
                self._pump(self._system, self._sys_q, "系统音频")))

        next_tick = loop.time() + TICK_S
        mic_carry = np.empty(0, dtype=np.float32)
        sys_carry = np.empty(0, dtype=np.float32)
        while True:
            # 固定 500ms 单调时钟节拍
            delay = next_tick - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            next_tick += TICK_S

            if not self._mic_alive and not self._sys_alive:
                # 两路均失败：停止并报告
                self._notify_error("mixed_all_failed", "混合模式两路均已失败，会话停止")
                break

            got_mic = self._drain_latest(self._mic_q)
            got_sys = self._drain_latest(self._sys_q)
            if got_mic is not None:
                mic_carry = np.concatenate([mic_carry, got_mic])
            if got_sys is not None:
                sys_carry = np.concatenate([sys_carry, got_sys])

            # 每个周期各取一个 500ms 块；缺少的一路补零
            a = mic_carry[:CHUNK_LEN] if mic_carry.size else np.zeros(CHUNK_LEN, dtype=np.float32)
            mic_carry = mic_carry[CHUNK_LEN:] if mic_carry.size > CHUNK_LEN else np.empty(0, dtype=np.float32)
            b = sys_carry[:CHUNK_LEN] if sys_carry.size else np.zeros(CHUNK_LEN, dtype=np.float32)
            sys_carry = sys_carry[CHUNK_LEN:] if sys_carry.size > CHUNK_LEN else np.empty(0, dtype=np.float32)
            if a.size < CHUNK_LEN:
                a = np.concatenate([a, np.zeros(CHUNK_LEN - a.size, dtype=np.float32)])
            if b.size < CHUNK_LEN:
                b = np.concatenate([b, np.zeros(CHUNK_LEN - b.size, dtype=np.float32)])

            # 同一周期只生成一个混合帧（不双倍推进时间轴）
            mixed = soft_mix(b, a)  # system + mic
            self._emitted += CHUNK_LEN
            yield AudioFrame(samples=mixed,
                             stream_time_ms=int(self._emitted / SAMPLE_RATE * 1000))

    async def stop(self) -> None:
        await self._mic.stop()
        await self._system.stop()
        for t in self._pump_tasks:
            if not t.done():
                t.cancel()
        if self._pump_tasks:
            await asyncio.gather(*self._pump_tasks, return_exceptions=True)
        self._pump_tasks.clear()

    def level(self) -> float:
        return max(self._mic.level(), self._system.level())

    def mic_level(self) -> float:
        return self._mic.level()

    def system_level(self) -> float:
        return self._system.level()
