"""音频设备枚举 API（AudioDeviceManager 归一化输出 + 设备测试）。

GET  /api/devices       归一化/去重/分类后的设备（recommended/others/advanced + defaults）
POST /api/devices/test  测试麦克风/系统音频（实时音量/信号检测）
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from ..audio import device_manager as dm

router = APIRouter(tags=["devices"])


@router.get("/api/devices")
async def list_devices() -> dict:
    # 设备枚举是阻塞 IO，放入线程池避免阻塞事件循环
    return await asyncio.to_thread(dm.list_devices_structured)


class DeviceTestRequest(BaseModel):
    id: str | None = None            # 稳定 id；None/空 = 默认设备
    direction: str = "input"         # input | output


@router.post("/api/devices/test")
async def test_device(payload: DeviceTestRequest) -> dict:
    if payload.direction == "output":
        return await asyncio.to_thread(dm.test_output_device, payload.id)
    return await asyncio.to_thread(dm.test_input_device, payload.id)
