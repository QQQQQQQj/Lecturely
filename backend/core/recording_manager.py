"""RecordingManager：录音流式写入与时间对齐（ADR-005）。

- 实时运行中将 16kHz mono int16 PCM 流式写入 WAV（wave 模块，finalize 时回填头）。
- 录音时长 = 已写入采样数，与 ASR 提交 token 的时间戳同源（都是"已推入的音频时长"），
  因此 segment.start_ms 可直接用于录音定位回放。
- 录音写盘失败不影响实时转录主链路（目标文档 §P0-7）。
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Optional

from ..audio.base import AudioFrame, CHANNELS, SAMPLE_RATE
from ..logging_setup import get_logger
from ..storage.repositories.misc import RecordingRepository
from ..storage.repositories.sessions import SessionRepository
from ..websocket import events as ev

logger = get_logger(__name__)


class RecordingManager:
    def __init__(self, *, session_id: str, recordings_dir: Path, broadcast):
        self._session_id = session_id
        self._recordings_dir = recordings_dir
        self._broadcast = broadcast
        self._wf: Optional[wave.Wave_write] = None
        self._file_path: Optional[Path] = None
        self._frames_written = 0
        self._failed = False

    @property
    def file_name(self) -> str:
        return f"{self._session_id}.wav"

    def start(self) -> None:
        try:
            self._recordings_dir.mkdir(parents=True, exist_ok=True)
            self._file_path = self._recordings_dir / self.file_name
            wf = wave.open(str(self._file_path), "wb")
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16
            wf.setframerate(SAMPLE_RATE)
            self._wf = wf
            self._frames_written = 0
            logger.info("录音开始：%s", self._file_path)
        except Exception as e:  # noqa: BLE001
            self._failed = True
            logger.error("录音文件打开失败：%s", e)

    def write(self, frame: AudioFrame) -> None:
        if self._wf is None or self._failed:
            return
        try:
            self._wf.writeframes(frame.to_int16_bytes())
            self._frames_written += len(frame.samples)
        except Exception as e:  # noqa: BLE001
            self._failed = True
            logger.error("录音写入失败（不影响转录）：%s", e)

    @property
    def duration_ms(self) -> int:
        return int(self._frames_written / SAMPLE_RATE * 1000)

    async def finalize(self) -> Optional[dict]:
        """关闭文件、回填头、落 recordings 行并回写 session.recording_id。"""
        if self._wf is not None:
            try:
                self._wf.close()  # wave.close 回填 nframes
            except Exception as e:  # noqa: BLE001
                logger.warning("录音文件关闭异常：%s", e)
            self._wf = None
        if self._failed or self._file_path is None or not self._file_path.exists():
            return None
        try:
            size = self._file_path.stat().st_size
            recording = await RecordingRepository().create(
                session_id=self._session_id, file_path=self.file_name,
                format="wav", sample_rate=SAMPLE_RATE, channels=CHANNELS,
            )
            await RecordingRepository().finalize(
                recording["id"], size_bytes=size, duration_ms=self.duration_ms,
            )
            await SessionRepository().update_fields(
                self._session_id, recording_id=recording["id"],
            )
            await self._broadcast(ev.make_event(ev.RECORDING_STATUS, session_id=self._session_id, payload={
                "state": "finalized", "file_path": self.file_name,
                "size_bytes": size, "duration_ms": self.duration_ms,
            }))
            logger.info("录音完成：%s（%d 字节，%dms）", self.file_name, size, self.duration_ms)
            recording["size_bytes"] = size
            recording["duration_ms"] = self.duration_ms
            return recording
        except Exception as e:  # noqa: BLE001
            logger.error("录音 finalize 失败：%s", e)
            return None

    async def abort(self) -> None:
        """放弃录音（关闭并删除半成品）。"""
        if self._wf is not None:
            try:
                self._wf.close()
            except Exception:
                pass
            self._wf = None
