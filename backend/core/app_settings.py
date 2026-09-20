"""应用设置服务：从 settings 表读取运行时配置（带默认值）。

配置在设置页修改后持久化到 settings 表；Provider 实例化时读取。
"""
from __future__ import annotations

from typing import Any

from ..storage.repositories.misc import SettingsRepository

DEFAULT_TRANSLATION: dict[str, Any] = {
    # 默认 ollama：便携包内置 Ollama+qwen2.5，开箱即可翻译；
    # mtran 需单独运行 MTranServer，用户可在设置页切换。
    "provider": "ollama",
    "mtran": {"endpoint": "http://127.0.0.1:8989", "timeout": 8, "retries": 1},
    "openai_compatible": {"base_url": "", "api_key": "", "model": "", "timeout": 30, "max_tokens": 512},
    "ollama": {"endpoint": "http://127.0.0.1:11434", "model": "qwen2.5:1.5b", "timeout": 60},
}

DEFAULT_AI: dict[str, Any] = {
    "provider": "ollama",
    "ollama": {"endpoint": "http://127.0.0.1:11434", "model": "qwen2.5:1.5b", "timeout": 120},
    "openai_compatible": {"base_url": "", "api_key": "", "model": "", "timeout": 120},
    # 会话问答 RAG 检索用的嵌入模型（本地，跨语言）
    "embedding": {"endpoint": "http://127.0.0.1:11434", "model": "bge-m3"},
}


def _merge(default: dict, override: dict) -> dict:
    result = dict(default)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = v
    return result


async def get_translation_config() -> dict[str, Any]:
    repo = SettingsRepository()
    saved = await repo.get("translation", {})
    return _merge(DEFAULT_TRANSLATION, saved or {})


async def get_ai_config() -> dict[str, Any]:
    repo = SettingsRepository()
    saved = await repo.get("ai", {})
    return _merge(DEFAULT_AI, saved or {})


async def get_provider_and_config(kind: str = "translation") -> tuple[str, dict[str, Any]]:
    """返回 (provider_name, provider_config_dict)。"""
    if kind == "translation":
        cfg = await get_translation_config()
    else:
        cfg = await get_ai_config()
    provider = cfg.get("provider", DEFAULT_TRANSLATION["provider"])
    provider_cfg = cfg.get(provider, {})
    return provider, provider_cfg
