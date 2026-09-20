"""统一麦克风格式协商/打开模块（根因 3）。

设备测试 API 与 MicSource 必须调用同一套 opener，消除"测试 16kHz 成功、
真实采集原生率失败"（或反之，如 Realtek WASAPI 仅接受原生 48000Hz，
强制 16000Hz 报 -9997 Invalid sample rate）的参数不一致问题。

协商顺序（每次失败的半初始化 stream 必须关闭）：
1. 原生采样率 + mono
2. 原生采样率 + 原生声道数（≤2）
3. 16000Hz + mono（兜底，部分 USB 麦支持）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..errors import AudioSourceError
from ..logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class NegotiatedFormat:
    """协商成功的真实打开参数。"""
    raw_id: int              # PortAudio 设备 index
    name: str                # 完整设备名
    host_api: str            # Host API 名称
    sample_rate: int         # 实际打开采样率
    channels: int            # 实际打开声道数

    def to_dict(self) -> dict:
        return {"raw_id": self.raw_id, "name": self.name, "host_api": self.host_api,
                "sample_rate": self.sample_rate, "channels": self.channels}


def _candidates(native_rate: int, max_ch: int) -> list[tuple[int, int]]:
    """按优先级生成 (rate, channels) 组合，去重保序。"""
    cands: list[tuple[int, int]] = [(native_rate, 1)]
    if max_ch >= 2:
        cands.append((native_rate, 2))
    if native_rate != 16000:
        cands.append((16000, 1))
    seen: set[tuple[int, int]] = set()
    out = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def negotiate_input_format(raw_index: Optional[int]) -> NegotiatedFormat:
    """探测设备可用的 (rate, channels) 组合，不留打开的流。

    raw_index=None 表示 PortAudio 默认输入（调用方应先经严格解析，
    正常路径不应再传 None——见 device_manager.resolve_input_strict）。
    """
    import sounddevice as sd
    try:
        info = sd.query_devices(raw_index, "input") if raw_index is not None \
            else sd.query_devices(kind="input")
    except Exception as e:
        raise AudioSourceError("mic_not_found", "未找到可用的麦克风设备",
                               "请检查麦克风是否连接，或在设置中更换输入设备") from e
    resolved_index = int(info.get("index", raw_index if raw_index is not None else -1))
    native_rate = int(info.get("default_samplerate", 16000))
    max_ch = max(1, int(info.get("max_input_channels", 1)))
    hostapis = sd.query_hostapis()
    host_name = ""
    try:
        host_name = hostapis[int(info.get("hostapi", 0))]["name"]
    except Exception:
        pass

    last_err: Optional[Exception] = None
    for rate, ch in _candidates(native_rate, max_ch):
        try:
            sd.check_input_settings(device=resolved_index if resolved_index >= 0 else None,
                                    samplerate=rate, channels=ch, dtype="float32")
            logger.info("麦克风格式协商成功：%s raw=%d %dHz ch=%d (%s)",
                        info.get("name", "?"), resolved_index, rate, ch, host_name)
            return NegotiatedFormat(raw_id=resolved_index, name=str(info.get("name", "")),
                                    host_api=host_name, sample_rate=rate, channels=ch)
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.debug("格式 %dHz/ch%d 不可用：%s", rate, ch, e)
    raise AudioSourceError("mic_format_unsupported",
                           f"麦克风不支持任何候选采集格式：{last_err}",
                           "请在系统声音设置中检查该设备，或更换输入设备") from last_err


def open_input_stream(fmt: NegotiatedFormat, callback, blocksize_ms: int = 100):
    """按协商结果打开输入流；失败时确保半初始化流被关闭。"""
    import sounddevice as sd
    blocksize = int(fmt.sample_rate * blocksize_ms / 1000)
    stream = None
    try:
        stream = sd.InputStream(
            samplerate=fmt.sample_rate, channels=fmt.channels, dtype="float32",
            blocksize=blocksize, device=fmt.raw_id if fmt.raw_id >= 0 else None,
            callback=callback,
        )
        stream.start()
        return stream
    except Exception as e:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        raise AudioSourceError("mic_open_failed", f"麦克风打开失败：{e}",
                               "可能被其他应用独占，或系统录音权限未开启") from e


def refresh_portaudio() -> None:
    """刷新 PortAudio 设备快照。

    长驻服务进程里 PortAudio 在 Pa_Initialize 时缓存设备拓扑；之后设备/路由变化
    会导致旧 index 打开到错误端点（如 WDM-KS 0x490）。仅在本进程无活动
    sounddevice 流时调用（会话启动/设备测试前满足此条件）。
    """
    import sounddevice as sd
    try:
        sd._terminate()
        sd._initialize()
        logger.info("PortAudio 设备快照已刷新")
    except Exception as e:  # noqa: BLE001
        logger.warning("刷新 PortAudio 失败：%s", e)


def find_input_index_by_name(name: str) -> Optional[int]:
    """刷新后按完整名重新定位输入设备 index（index 刷新后可能漂移）。"""
    import sounddevice as sd
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and d.get("name") == name:
                return i
    except Exception:
        return None
    return None


def negotiate_and_open_resilient(raw_index: Optional[int], callback,
                                 blocksize_ms: int = 100) -> tuple:
    """协商+打开；失败则刷新 PortAudio 快照、按名称重解析后重试一次。

    返回 (stream, fmt)。重试仍失败则抛 AudioSourceError。
    """
    fmt = negotiate_input_format(raw_index)
    try:
        return open_input_stream(fmt, callback, blocksize_ms), fmt
    except AudioSourceError as first_err:
        logger.warning("麦克风打开失败（%s），刷新 PortAudio 快照后重试…", first_err)
        refresh_portaudio()
        idx = find_input_index_by_name(fmt.name)
        fmt2 = negotiate_input_format(idx if idx is not None else raw_index)
        return open_input_stream(fmt2, callback, blocksize_ms), fmt2
