"""ASR 运行时解析（根因 8）：device / compute_type 的安全决策。

问题：便携包机器上 CTranslate2 能看到 GPU，但缺少 cublas64_12.dll，
bundled WhisperLiveKit 使用 device=auto/compute_type=auto 会错误选择 CUDA，
运行时报 "Library cublas64_12.dll is not found or cannot be loaded"。

策略：
- auto 模式必须同时满足「有 GPU」且「CUDA 12 运行库（cuBLAS/cuDNN）实际可加载」
  才允许 CUDA；否则回退 CPU/int8 并记录清楚原因。
- 已实测 CPU/int8 转写 14.5 秒录音仅 ~0.88s（RTF≈0.061），性能足够实时，
  不要试图通过下载庞大 CUDA 依赖解决。

解析结果通过环境变量 LECTURELY_ASR_DEVICE / LECTURELY_ASR_COMPUTE_TYPE
透传给 whisperlivekit 的 FasterWhisperASR（已改为读取该变量），
最终传入 faster-whisper WhisperModel。
"""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

from ..logging_setup import get_logger

logger = get_logger(__name__)

# CUDA 12 推理所需的关键运行库（faster-whisper/CTranslate2 GPU 路径）
_CUDA_REQUIRED_DLLS = ("cublas64_12.dll", "cudnn64_9.dll")

ENV_DEVICE = "LECTURELY_ASR_DEVICE"
ENV_COMPUTE = "LECTURELY_ASR_COMPUTE_TYPE"


@dataclass
class ASRRuntime:
    device: str          # cpu | cuda
    compute_type: str    # int8 | float16 | ...
    reason: str          # 决策原因（写日志/诊断）

    def to_dict(self) -> dict:
        return {"device": self.device, "compute_type": self.compute_type, "reason": self.reason}


def _dll_loadable(name: str) -> bool:
    try:
        ctypes.WinDLL(name)
        return True
    except Exception:
        return False


def _cuda_gpu_visible() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _cuda_runtime_complete() -> tuple[bool, str]:
    """检查 CUDA 12 运行库是否全部可实际加载。"""
    if os.name != "nt":
        return True, "非 Windows，跳过 DLL 检查"
    missing = [d for d in _CUDA_REQUIRED_DLLS if not _dll_loadable(d)]
    if missing:
        return False, f"CUDA 运行库不完整（无法加载：{', '.join(missing)}）"
    return True, "CUDA 运行库完整"


def resolve_asr_runtime(device: str = "auto", compute_type: str = "int8") -> ASRRuntime:
    """解析有效 device/compute_type。

    - device=cpu：直接 CPU。
    - device=cuda：仍校验运行库，缺失则回退 CPU（避免运行时崩溃）。
    - device=auto：GPU 可见 且 运行库完整 才用 CUDA，否则 CPU/int8。
    """
    device = (device or "auto").lower()
    compute_type = (compute_type or "int8").lower()

    if device == "cpu":
        rt = ASRRuntime("cpu", compute_type if compute_type != "auto" else "int8",
                        "配置指定 CPU")
        _apply_env(rt)
        return rt

    gpu = _cuda_gpu_visible()
    libs_ok, libs_reason = _cuda_runtime_complete()

    if gpu and libs_ok:
        ct = compute_type if compute_type != "auto" else "float16"
        rt = ASRRuntime("cuda", ct, f"GPU 可用且{libs_reason}")
    else:
        why = []
        if not gpu:
            why.append("未检测到可用 GPU")
        if not libs_ok:
            why.append(libs_reason)
        ct = compute_type if compute_type not in ("auto", "float16") else "int8"
        rt = ASRRuntime("cpu", ct, "回退 CPU：" + "；".join(why)
                        + "（实测 CPU/int8 RTF≈0.06，满足实时）")
        if device == "cuda":
            logger.warning("配置要求 CUDA 但%s，已强制回退 CPU 以避免崩溃", "；".join(why))
    logger.info("ASR 运行时：device=%s compute_type=%s（%s）",
                rt.device, rt.compute_type, rt.reason)
    _apply_env(rt)
    return rt


def _apply_env(rt: ASRRuntime) -> None:
    """把有效值透传给 whisperlivekit/faster-whisper（backends.py 读取）。"""
    os.environ[ENV_DEVICE] = rt.device
    os.environ[ENV_COMPUTE] = rt.compute_type
