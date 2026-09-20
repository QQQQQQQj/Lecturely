"""健康检查与诊断（P0-1 服务状态可见；§15 诊断）。"""
from __future__ import annotations

import platform
import sys

from fastapi import APIRouter

from .. import __version__
from ..config import get_config

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": "Lecturely",
        "version": __version__,
    }


@router.get("/api/diagnostics")
async def diagnostics() -> dict:
    cfg = get_config()
    return {
        "app": "Lecturely",
        "version": __version__,
        "os": f"{platform.system()} {platform.release()}",
        "python": sys.version.split()[0],
        "host": cfg.host,
        "port": cfg.port,
        "data_dir": str(cfg.data_dir),
        "gpu": _gpu_info(),
        "asr": {"backend": cfg.asr_backend, "model": cfg.asr_model,
                "device": cfg.asr_device, "compute_type": cfg.asr_compute_type},
    }


def _gpu_info() -> dict:
    """检测 CUDA 可用性（不假设有 NVIDIA GPU）。"""
    try:
        import torch
        if torch.cuda.is_available():
            return {"cuda_available": True, "device_name": torch.cuda.get_device_name(0),
                    "cuda_version": torch.version.cuda}
        return {"cuda_available": False, "device_name": None, "cuda_version": None}
    except Exception:
        return {"cuda_available": False, "device_name": None, "cuda_version": None}
