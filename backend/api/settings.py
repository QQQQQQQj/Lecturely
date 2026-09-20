"""设置 API（P0-12：配置持久化）与 Provider 健康测试。

- GET  /api/settings/{section}   读取某组配置（translation / ai / audio / asr / appearance）
- PUT  /api/settings/{section}   保存某组配置
- POST /api/settings/translation/test  测试翻译 Provider 连通性
- POST /api/settings/ai/test           测试 AI Provider 连通性
- GET  /api/settings/providers/translation  列出可用翻译 Provider
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.app_settings import get_ai_config, get_translation_config
from ..errors import LecturelyError
from ..storage.repositories.misc import SettingsRepository
from ..translation import available_providers, create_provider

router = APIRouter(tags=["settings"])

_SECTIONS = {"translation", "ai", "audio", "asr", "appearance", "storage"}


@router.get("/api/settings/providers/translation")
async def list_translation_providers() -> dict:
    return {"providers": available_providers()}


@router.get("/api/settings/{section}")
async def get_settings(section: str) -> dict:
    if section == "translation":
        return await get_translation_config()
    if section == "ai":
        return await get_ai_config()
    if section in _SECTIONS:
        return await SettingsRepository().get(section, {})
    return {"error": f"未知设置组：{section}"}


class SectionPayload(BaseModel):
    values: dict[str, Any]


@router.put("/api/settings/{section}")
async def put_settings(section: str, payload: SectionPayload) -> dict:
    if section not in _SECTIONS:
        return {"error": f"未知设置组：{section}"}
    await SettingsRepository().set(section, payload.values)
    return {"ok": True, "section": section}


@router.post("/api/settings/translation/test")
async def test_translation(payload: dict[str, Any]) -> dict:
    """测试翻译 Provider 连通性。body: {"provider": "mtran", "config": {...}}"""
    provider_name = payload.get("provider", "mtran")
    config = payload.get("config", {})
    try:
        provider = create_provider(provider_name, config)
        health = await provider.health()
        return {"ok": health.ok, "latency_ms": health.latency_ms, "message": health.message}
    except LecturelyError as e:
        return {"ok": False, "message": e.info.message, "suggestion": e.info.suggestion}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
