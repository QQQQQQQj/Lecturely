"""TranslationProvider 抽象与注册表（对应 docs/architecture/05 §4）。

业务层只面向本接口；通过 create_provider(name, config) 从注册表实例化。
所有实现必须具备：超时、可读错误（ProviderError）、健康检查。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from ..errors import ProviderError


@dataclass
class TranslateRequest:
    text: str
    source_lang: str            # "auto" 允许
    target_lang: str
    context: list[tuple[str, str]] = field(default_factory=list)  # 最近 N 句 (原文, 译文)


@dataclass
class TranslateResult:
    text: str
    provider: str
    from_cache: bool = False


@dataclass
class ProviderHealth:
    ok: bool
    latency_ms: Optional[float] = None
    message: str = ""


class TranslationProvider(Protocol):
    name: str

    async def translate(self, req: TranslateRequest) -> TranslateResult: ...
    async def health(self) -> ProviderHealth: ...
    def supported_languages(self) -> Optional[list[str]]: ...
    def supports_context(self) -> bool: ...


# ---- 语言码归一化（zh-CN → zh-Hans 等，供 mtran 等使用）----
_LANG_ALIAS = {
    "zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-hans": "zh-Hans",
    "zh-tw": "zh-Hant", "zh-hant": "zh-Hant",
    "auto": "auto",
}


def normalize_lang(code: str) -> str:
    if not code:
        return code
    return _LANG_ALIAS.get(code.lower(), code)


# ---- 注册表 ----
_REGISTRY: dict[str, type] = {}


def register(name: str, provider_cls: type) -> None:
    _REGISTRY[name] = provider_cls


def create_provider(name: str, config: dict[str, Any]) -> TranslationProvider:
    if name not in _REGISTRY:
        raise ProviderError("translation", "provider_unknown",
                            f"未知的翻译 Provider：{name}",
                            suggestion="请在设置页选择可用的翻译服务")
    return _REGISTRY[name](config)  # type: ignore[call-arg]


def available_providers() -> list[str]:
    return list(_REGISTRY.keys())
