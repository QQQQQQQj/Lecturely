"""文件采集源（文件上传转录）。

用 ffmpeg 子进程将任意格式（wav/mp3/m4a/flac/mp4/mkv/mov）解码为 16kHz mono PCM，
以非实时（尽可能快）节奏产出 AudioFrame，下游管线与实时源完全一致（实时/批处理复用同一抽象）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator, Optional

import numpy as np

from ..errors import AudioSourceError
from ..logging_setup import get_logger
from .base import AudioFrame, InputMode, SAMPLE_RATE
from .ffmpeg_utils import get_ffmpeg_path, probe_duration_ms

logger = get_logger(__name__)

CHUNK_MS = 500


class FileSource:
    def __init__(self, file_path: str, realtime: bool = False):
        self.mode = InputMode.FILE
        self._file_path = file_path
        self.name = Path(file_path).name
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._realtime = realtime
        self._emitted = 0
        self._duration_ms = probe_duration_ms(file_path)
        self._running = False

    @property
    def duration_ms(self) -> Optional[int]:
        return self._duration_ms

    async def start(self) -> None:
        if not Path(self._file_path).is_file():
            raise AudioSourceError("file_not_found", "上传的文件不存在", "请重新上传")
        try:
            ffmpeg = get_ffmpeg_path()
        except RuntimeError as e:
            raise AudioSourceError("ffmpeg_missing", "未找到 ffmpeg", "请运行安装脚本") from e
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", self._file_path,
            "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-",  # 输出到 stdout
        ]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            raise AudioSourceError("file_decode_failed", f"文件解码失败：{e}",
                                   "文件格式可能不受支持或已损坏") from e
        self._running = True
        self._emitted = 0
        logger.info("文件解码开始：%s", self._file_path)

    async def frames(self) -> AsyncIterator[AudioFrame]:
        chunk_bytes = int(SAMPLE_RATE * CHUNK_MS / 1000) * 2  # int16
        assert self._proc is not None and self._proc.stdout is not None
        while self._running:
            try:
                data = await self._proc.stdout.read(chunk_bytes)
            except Exception as e:  # noqa: BLE001
                logger.warning("读取解码流失败：%s", e)
                break
            if not data:
                break  # EOF
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            self._emitted += len(samples)
            yield AudioFrame(samples=samples,
                             stream_time_ms=int(self._emitted / SAMPLE_RATE * 1000))
            if self._realtime:
                await asyncio.sleep(CHUNK_MS / 1000)
            else:
                await asyncio.sleep(0)  # 让出事件循环但尽快

    async def stop(self) -> None:
        self._running = False
        if self._proc is not None:
            try:
                if self._proc.returncode is None:
                    self._proc.kill()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def level(self) -> float:
        return 0.0

    def progress(self) -> Optional[float]:
        """解码进度（0~1），基于已产出时长与总时长。"""
        if not self._duration_ms:
            return None
        cur = int(self._emitted / SAMPLE_RATE * 1000)
        return min(cur / self._duration_ms, 1.0)
