"""AudioDeviceManager：音频设备统一枚举、归一化、去重、分类与推荐。

目标（优化指示文档 §2-11）：把底层 12~20 条原始设备归一化为普通用户可见的 2~5 个有意义设备。
- 过滤：0 通道 / Sound Mapper / Primary Capture / loopback（麦克风列表）/ 输出设备不混入麦克风
- 去重：同一物理设备经 MME/DirectSound/WASAPI 重复暴露只保留一个（Windows 优先 WASAPI）
- 分类：microphone / microphone_array / headset_microphone / speaker / headphone / virtual
- 推荐：默认 + 独立物理设备
- 输出：简洁（recommended/others）+ 高级（advanced 全部原始 endpoint）
"""
from __future__ import annotations

import hashlib
import platform
import re
from dataclasses import dataclass, field
from typing import Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)

# 兼容层/映射入口关键词（不应平铺给普通用户）
_MAPPER_KEYWORDS = [
    "sound mapper", "声音映射器", "primary sound capture", "主声音捕获",
    "primary sound driver", "wave link", "default input", "default output",
]
# 虚拟设备关键词
_VIRTUAL_KEYWORDS = [
    "virtual", "vb-audio", "vb audio", "voicemeeter", "voice meeter", "cable",
    "stereo mix", "立体声混音", "wave link", "null sink", "monitor",
]
# 麦克风阵列关键词
_ARRAY_KEYWORDS = ["array", "阵列"]
# 耳机关键词
_HEADSET_KEYWORDS = [
    "headset", "headphone", "earphone", "earbuds", "耳机", "耳麦", "蓝牙", "bluetooth",
    "airpods", "buds", "hands-free", "免提",
]
_SPEAKER_KEYWORDS = ["speaker", "扬声器", "output", "hdmi", "display", "line out", "spdif", "digital"]

# Host API 优先级（Windows 优先 WASAPI）
_HOST_PRIORITY = {"WASAPI": 3, "Windows WASAPI": 3, "CoreAudio": 2, "ALSA": 2, "DirectSound": 1, "MME": 0}


@dataclass
class AudioDevice:
    id: str                       # 稳定 id（归一化名+方向 的哈希）
    raw_id: str                   # 底层原始 index（当前枚举）
    name: str                     # 原始名
    display_name: str             # 归一化展示名
    sub_name: str = ""            # 次级驱动信息
    direction: str = "input"      # input | output | loopback
    device_type: str = "unknown"  # microphone | microphone_array | headset_microphone | speaker | headphone | virtual | unknown
    host_api: str = ""
    is_default: bool = False
    is_comm_default: bool = False
    is_virtual: bool = False
    is_loopback: bool = False
    is_available: bool = True
    recommended: bool = False
    channels: int = 1
    sample_rate: int = 16000
    hardware_group_id: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "raw_id": self.raw_id, "name": self.name,
            "display_name": self.display_name, "sub_name": self.sub_name,
            "direction": self.direction, "device_type": self.device_type,
            "host_api": self.host_api, "is_default": self.is_default,
            "is_comm_default": self.is_comm_default, "is_virtual": self.is_virtual,
            "is_loopback": self.is_loopback, "is_available": self.is_available,
            "recommended": self.recommended, "channels": self.channels,
            "sample_rate": self.sample_rate, "hardware_group_id": self.hardware_group_id,
        }


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _norm_name(name: str) -> str:
    """归一化设备名（用于分组去重）。

    关键（根因 1）：**保留括号内的硬件身份**（ToDesk / XIBERIA / Intel Smart Sound 等），
    否则“麦克风 (ToDesk Virtual Audio)”与“麦克风 (XIBERIA T10)”会被合并成同一稳定 ID，
    真实物理麦从列表消失。仅做：小写、去方向后缀、去尾部序号、压缩空白、
    统一中英括号（嵌套括号内容原样保留，避免 Realtek WASAPI 与 WDM-KS 端点错误去重）。
    """
    s = name.strip().lower()
    # 去 " - Input" / " - Output" / " [Loopback]" 等方向后缀
    s = re.sub(r"\s*[-–]\s*(input|output|输入|输出)\s*$", "", s)
    s = re.sub(r"\s*\[(loopback|环回)\]\s*$", "", s)
    # 统一全角括号为半角（保留内容）
    s = s.replace("（", "(").replace("）", ")")
    # 去尾部序号 " 1/2/3"（不在括号内的裸序号，可能多个）
    s = re.sub(r"(\s+\d+)+\s*(?=\(|$)", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_truncated_variant(short: str, long: str) -> bool:
    """MME/DirectSound 会把长名截断到 ~31 字符；短名是长名前缀且长度接近截断阈值时，
    视为同一物理设备的截断变体（仅用于跨 Host API 合并，不会合并不同硬件）。"""
    if len(short) >= len(long):
        return False
    return len(short) >= 28 and long.startswith(short)


def _is_mapper(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in _MAPPER_KEYWORDS)


def _is_virtual(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in _VIRTUAL_KEYWORDS)


def _looks_like_output(name: str) -> bool:
    """判断一个“输入设备”是否其实是误暴露的输出端点（如 WDM-KS 把扬声器暴露为捕获）。"""
    n = name.lower()
    return any(k in n for k in ["speaker", "扬声器", "output", "输出", "line out", "spdif", "digital output"])


def _classify_type(name: str, direction: str) -> str:
    n = name.lower()
    if _is_virtual(name):
        return "virtual"
    if direction in ("output", "loopback"):
        if any(k in n for k in _HEADSET_KEYWORDS):
            return "headphone"
        return "speaker"
    # input
    if any(k in n for k in _ARRAY_KEYWORDS):
        return "microphone_array"
    if any(k in n for k in _HEADSET_KEYWORDS):
        return "headset_microphone"
    return "microphone"


def _stable_id(norm_name: str, direction: str) -> str:
    """稳定 id：归一化名+方向的哈希（重启不漂移，只要设备名不变）。"""
    key = f"{norm_name}::{direction}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


def _split_display(name: str) -> tuple[str, str]:
    """把长设备名拆成 主名称 + 次级驱动信息。"""
    # 常见模式："麦克风阵列 (Intel Smart Sound Technology OED)"
    m = re.match(r"^(.*?)\s*[（(](.*?)[）)]\s*$", name.strip())
    if m and m.group(1).strip() and m.group(2).strip():
        return m.group(1).strip(), m.group(2).strip()
    # "Microphone (Realtek Audio)" 同理已被上面覆盖
    return name.strip(), ""


def _host_priority(host: str) -> int:
    return _HOST_PRIORITY.get(host, 1)


def _dedupe(devices: list[AudioDevice]) -> list[AudioDevice]:
    """按 hardware_group_id 去重；同组保留 Host API 优先级最高者（Windows 优先 WASAPI）。

    跨 Host API 同硬件合并支持 MME 截断名（前缀匹配）；不同物理设备（括号硬件名不同）
    永不相互覆盖。"""
    groups: dict[str, AudioDevice] = {}
    for d in devices:
        gid = d.hardware_group_id or d.id
        # 截断变体归入已有组（取更长名为组键）
        matched = None
        for key in groups:
            if key == gid or _is_truncated_variant(gid, key) or _is_truncated_variant(key, gid):
                matched = key
                break
        if matched is None:
            groups[gid] = d
            continue
        cur = groups[matched]
        if _host_priority(d.host_api) > _host_priority(cur.host_api):
            if cur.is_default:
                d.is_default = True
            # 组键升级为更完整（更长）的名字
            if len(gid) > len(matched):
                del groups[matched]
                groups[gid] = d
            else:
                groups[matched] = d
        elif d.is_default:
            cur.is_default = True
    return list(groups.values())


def _enumerate_inputs() -> list[AudioDevice]:
    """枚举输入设备（sounddevice / PortAudio）。"""
    result: list[AudioDevice] = []
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        default_in = sd.default.device[0] if sd.default.device else None
        comm_in = getattr(sd.default, "device", [None, None])[0]
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) <= 0:
                continue
            name = d.get("name", f"设备 {i}")
            if _is_mapper(name):
                continue
            if _looks_like_output(name):
                continue  # 误暴露为输入的输出端点（如“电脑扬声器 output”）
            host = hostapis[d.get("hostapi", 0)]["name"] if d.get("hostapi") is not None else ""
            norm = _norm_name(name)
            disp, sub = _split_display(name)
            dev = AudioDevice(
                id=_stable_id(norm, "input"), raw_id=str(i), name=name,
                display_name=disp or name, sub_name=sub,
                direction="input", device_type=_classify_type(name, "input"),
                host_api=host, is_default=(i == default_in),
                is_comm_default=(i == comm_in),
                is_virtual=_is_virtual(name), is_loopback=False,
                channels=int(d.get("max_input_channels", 1)),
                sample_rate=int(d.get("default_samplerate", 16000)),
                hardware_group_id=norm,
            )
            result.append(dev)
    except Exception as e:  # noqa: BLE001
        logger.warning("枚举输入设备失败：%s", e)
    return result


def _enumerate_outputs() -> list[AudioDevice]:
    """枚举输出设备（pyaudiowpatch，Windows）。"""
    result: list[AudioDevice] = []
    if not _is_windows():
        return result
    try:
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        try:
            wasapi_idx = None
            default_out_name = None
            for i in range(pa.get_host_api_count()):
                info = pa.get_host_api_info_by_index(i)
                if "WASAPI" in info["name"]:
                    wasapi_idx = info["index"]
                    try:
                        default_out_name = pa.get_device_info_by_index(info["defaultOutputDevice"])["name"]
                    except Exception:
                        pass
                    break
            if wasapi_idx is None:
                return result
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if d["hostApi"] != wasapi_idx or d["maxOutputChannels"] <= 0:
                    continue
                if d.get("isLoopbackDevice", False):
                    continue  # loopback 不作为用户可见输出设备
                name = d["name"]
                if _is_mapper(name):
                    continue
                norm = _norm_name(name)
                disp, sub = _split_display(name)
                dev = AudioDevice(
                    id=_stable_id(norm, "output"), raw_id=str(i), name=name,
                    display_name=disp or name, sub_name=sub,
                    direction="output", device_type=_classify_type(name, "output"),
                    host_api="WASAPI", is_default=(name == default_out_name),
                    is_virtual=_is_virtual(name), is_loopback=False,
                    channels=int(d["maxOutputChannels"]),
                    sample_rate=int(d["defaultSampleRate"]),
                    hardware_group_id=norm,
                )
                result.append(dev)
        finally:
            pa.terminate()
    except Exception as e:  # noqa: BLE001
        logger.warning("枚举输出设备失败：%s", e)
    return result


_UNSTABLE_HOSTS = ("wdm-ks", "windows wdm-ks")


def _is_unstable_host(host: str) -> bool:
    return host.strip().lower() in _UNSTABLE_HOSTS or "wdm" in host.lower()


def _mark_recommended(devices: list[AudioDevice]) -> None:
    """推荐：默认设备 + 非虚拟的物理设备；WDM-KS 等不稳定端点永不推荐（根因 1）。"""
    for d in devices:
        if _is_unstable_host(d.host_api):
            d.recommended = False
            continue
        d.recommended = d.is_default or (not d.is_virtual and d.device_type != "unknown")


def _categorize(devices: list[AudioDevice]) -> dict:
    """分为 recommended / others / advanced；WDM-KS 只能出现在 advanced。"""
    simple = [d for d in devices if not d.is_virtual and not _is_unstable_host(d.host_api)]
    recommended = [d for d in simple if d.recommended]
    others = [d for d in simple if not d.recommended]
    return {
        "recommended": [d.to_dict() for d in recommended],
        "others": [d.to_dict() for d in others],
        "advanced": [d.to_dict() for d in devices],  # 全部（含虚拟与 WDM-KS）
    }


def list_devices_structured() -> dict:
    """返回归一化、去重、分类后的设备结构。

    defaults 区分（根因 2）：
    - os_default_*：操作系统原始默认（可能是 ToDesk 等虚拟设备，仅展示说明）；
    - input_id/output_id：Lecturely 推荐默认（系统默认为虚拟时优先物理 WASAPI）。
    """
    inputs = _dedupe(_enumerate_inputs())
    outputs = _dedupe(_enumerate_outputs())
    _mark_recommended(inputs)
    _mark_recommended(outputs)

    os_default_in = next((d for d in inputs if d.is_default), None)
    os_default_out = next((d for d in outputs if d.is_default), None)
    eff_in = _effective_default_input(inputs)
    eff_out = os_default_out
    return {
        "inputs": _categorize(inputs),
        "outputs": _categorize(outputs),
        "defaults": {
            "input_id": eff_in.id if eff_in else None,
            "output_id": eff_out.id if eff_out else None,
            "input_name": eff_in.display_name if eff_in else None,
            "output_name": eff_out.display_name if eff_out else None,
            "os_default_input_id": os_default_in.id if os_default_in else None,
            "os_default_input_name": os_default_in.display_name if os_default_in else None,
            "os_default_is_virtual": bool(os_default_in.is_virtual) if os_default_in else False,
            "os_default_output_id": os_default_out.id if os_default_out else None,
            "os_default_output_name": os_default_out.display_name if os_default_out else None,
        },
    }


def _effective_default_input(inputs: list[AudioDevice]) -> Optional[AudioDevice]:
    """Lecturely 可用默认输入：系统默认非虚拟则用它；否则优先物理 WASAPI 输入。"""
    os_default = next((d for d in inputs if d.is_default), None)
    if os_default is not None and not os_default.is_virtual:
        return os_default
    physical = [d for d in inputs
                if not d.is_virtual and not _is_unstable_host(d.host_api)]
    wasapi = [d for d in physical if "wasapi" in d.host_api.lower()]
    if wasapi:
        return wasapi[0]
    if physical:
        return physical[0]
    return os_default


def resolve_input_strict(stable_id: Optional[str]) -> tuple[str, str]:
    """严格解析输入设备（根因 2）：

    - "default"/None → 解析为明确的 raw endpoint（不再交 None 给 PortAudio）；
      系统默认为虚拟设备时优先物理 WASAPI 输入。
    - 显式传入的陈旧 ID 不存在 → 抛 device_not_found，禁止静默回退。
    返回 (raw_id, display_name)。
    """
    from ..errors import AudioSourceError
    inputs = _dedupe(_enumerate_inputs())
    if not stable_id or stable_id == "default":
        eff = _effective_default_input(inputs)
        if eff is None:
            raise AudioSourceError("mic_not_found", "未找到任何可用的麦克风设备",
                                   "请检查麦克风是否连接")
        logger.info("default 解析为明确端点：%s (raw=%s, %s)",
                    eff.display_name, eff.raw_id, eff.host_api)
        return eff.raw_id, eff.display_name
    for d in inputs:
        if d.id == stable_id:
            return d.raw_id, d.display_name
    raise AudioSourceError("device_not_found",
                           f"指定的麦克风设备不存在（id={stable_id}）",
                           "设备可能已拔出，请刷新设备列表重新选择")


def resolve_output_strict(stable_id: Optional[str]) -> tuple[Optional[str], str, Optional[int]]:
    """严格解析输出设备（根因 6 配套）：返回 (raw_index 或 None 表示默认, display_name, raw_index_int)。

    - "default"/None → (None, 默认名, None)：SystemSource 用官方 get_default_wasapi_loopback。
    - 显式 ID 不存在 → device_not_found，禁止任意 fallback。
    """
    from ..errors import AudioSourceError
    outputs = _dedupe(_enumerate_outputs())
    if not stable_id or stable_id == "default":
        d = next((x for x in outputs if x.is_default), None)
        return None, (d.display_name if d else "默认系统音频"), None
    for d in outputs:
        if d.id == stable_id:
            return d.raw_id, d.display_name, int(d.raw_id)
    raise AudioSourceError("device_not_found",
                           f"指定的系统音频设备不存在（id={stable_id}）",
                           "设备可能已移除，请刷新设备列表重新选择")


def resolve_input(stable_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """把稳定 id 解析为当前 sounddevice index。返回 (raw_id, display_name)；不存在返回 (None, None)。"""
    if not stable_id or stable_id == "default":
        return None, None
    for d in _dedupe(_enumerate_inputs()):
        if d.id == stable_id:
            return d.raw_id, d.display_name
    return None, None


def resolve_output(stable_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """把稳定 id 解析为当前输出设备名（SystemSource 用）。返回 (name, display_name)；不存在返回 (None, None)。"""
    if not stable_id or stable_id == "default":
        return None, None
    for d in _dedupe(_enumerate_outputs()):
        if d.id == stable_id:
            return d.name, d.display_name
    return None, None


def test_input_device(stable_id: Optional[str], duration: float = 1.0) -> dict:
    """测试麦克风（根因 3）：与 MicSource 共用同一套 opener/格式协商，
    按真实采集参数短时采集计算 RMS；返回真实打开的 raw ID/名称/Host API/采样率/声道。"""
    try:
        import numpy as np
        import sounddevice as sd
        from .mic_opener import negotiate_and_open_resilient
        raw_id, name = resolve_input_strict(stable_id)
        frames: list = []

        def _cb(indata, frame_count, time_info, status):  # noqa: ANN001
            frames.append(indata.copy())

        # 与 MicSource 同一韧性 opener（协商+快照过期自动刷新重试）
        stream, fmt = negotiate_and_open_resilient(int(raw_id), _cb)
        try:
            sd.sleep(int(duration * 1000))
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if not frames:
            return {"ok": False, "level": 0.0, "has_signal": False, "message": "未采集到数据",
                    "format": fmt.to_dict()}
        data = np.concatenate(frames)
        if data.ndim > 1:
            data = data.mean(axis=1)
        rms = float(np.sqrt(np.mean(data ** 2)))
        level = min(rms * 8.0, 1.0)
        has_signal = rms > 0.005
        return {
            "ok": True, "level": round(level, 3), "has_signal": has_signal,
            "message": ("检测到声音" if has_signal else "无信号（请对着麦克风说话）"),
            "device": name, "format": fmt.to_dict(),
        }
    except Exception as e:  # noqa: BLE001
        code = getattr(getattr(e, "info", None), "code", "")
        return {"ok": False, "level": 0.0, "has_signal": False,
                "code": code, "message": f"麦克风不可用：{e}"}


def test_output_device(stable_id: Optional[str], duration: float = 1.2) -> dict:
    """测试系统音频：loopback 短时采集，检测是否有信号。"""
    if not _is_windows():
        return {"ok": False, "level": 0.0, "has_signal": False, "message": "仅 Windows 支持系统音频"}
    try:
        import numpy as np
        import pyaudiowpatch as pyaudio
        name, disp = resolve_output(stable_id)
        pa = pyaudio.PyAudio()
        try:
            wasapi_idx = None
            target = None
            for i in range(pa.get_host_api_count()):
                info = pa.get_host_api_info_by_index(i)
                if "WASAPI" in info["name"]:
                    wasapi_idx = info["index"]
                    break
            if wasapi_idx is None:
                return {"ok": False, "level": 0.0, "has_signal": False, "message": "未找到 WASAPI"}
            # 找对应的 loopback 设备
            default_out = pa.get_device_info_by_index(
                pa.get_host_api_info_by_index(wasapi_idx)["defaultOutputDevice"])["name"]
            target_name = name or default_out
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if (d["hostApi"] == wasapi_idx and d.get("isLoopbackDevice", False)
                        and target_name in d["name"]):
                    target = d
                    break
            if target is None:
                return {"ok": False, "level": 0.0, "has_signal": False, "message": "未找到回环设备"}
            rate = int(target["defaultSampleRate"])
            ch = int(target["maxInputChannels"])
            stream = pa.open(format=pyaudio.paFloat32, channels=ch, rate=rate, input=True,
                             input_device_index=target["index"],
                             frames_per_buffer=int(rate * 0.1))
            stream.start_stream()
            import time as _t
            chunks = []
            t0 = _t.time()
            while _t.time() - t0 < duration:
                if stream.get_read_available() >= int(rate * 0.1):
                    chunks.append(np.frombuffer(
                        stream.read(int(rate * 0.1), exception_on_overflow=False), dtype=np.float32))
                else:
                    _t.sleep(0.02)
            stream.stop_stream()
            stream.close()
            if not chunks:
                return {"ok": True, "level": 0.0, "has_signal": False,
                        "message": "无信号（请播放电脑声音）", "device": disp}
            data = np.concatenate(chunks)
            if ch > 1:
                data = data.reshape(-1, ch).mean(axis=1)
            rms = float(np.sqrt(np.mean(data ** 2)))
            has_signal = rms > 0.003
            return {
                "ok": True, "level": round(min(rms * 8.0, 1.0), 3), "has_signal": has_signal,
                "message": ("检测到系统音频" if has_signal else "无信号（请播放电脑声音）"),
                "device": disp,
            }
        finally:
            pa.terminate()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "level": 0.0, "has_signal": False, "message": f"系统音频不可用：{e}"}
