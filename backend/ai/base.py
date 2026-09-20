"""AIProvider 抽象与注册表（对应 docs/architecture/05 §5）。

用于纪要（summary）、问答（qa）、关键词提取。与翻译共用配置结构但注册表独立。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from ..errors import ProviderError
from ..translation.base import ProviderHealth


@dataclass
class ChatMessage:
    role: str            # system | user | assistant
    content: str


@dataclass
class ChatResult:
    text: str
    provider: str
    model: str = ""


class AIProvider(Protocol):
    name: str

    async def chat(self, messages: list[ChatMessage], *, timeout: float = 120,
                   max_tokens: int = 2048, temperature: float = 0.3) -> ChatResult: ...
    async def list_models(self) -> list[str]: ...
    async def health(self) -> ProviderHealth: ...


_REGISTRY: dict[str, type] = {}


def register_ai(name: str, provider_cls: type) -> None:
    _REGISTRY[name] = provider_cls


def create_ai_provider(name: str, config: dict[str, Any]) -> AIProvider:
    if name not in _REGISTRY:
        raise ProviderError("ai", "ai_provider_unknown", f"未知的 AI Provider：{name}",
                            suggestion="请在设置页选择可用的 AI 服务")
    return _REGISTRY[name](config)  # type: ignore[call-arg]


def available_ai_providers() -> list[str]:
    return list(_REGISTRY.keys())
