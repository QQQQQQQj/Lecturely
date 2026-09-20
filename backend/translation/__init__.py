"""翻译层：TranslationProvider 抽象 + 注册表 + 本地/在线实现（ADR-004）。"""
from .base import (
    ProviderHealth, TranslateRequest, TranslateResult, TranslationProvider,
    available_providers, create_provider, normalize_lang, register,
)
from .mtran import MTranProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatibleProvider

# 注册内置 Provider
register(MTranProvider.name, MTranProvider)
register(OllamaProvider.name, OllamaProvider)
register(OpenAICompatibleProvider.name, OpenAICompatibleProvider)

__all__ = [
    "ProviderHealth", "TranslateRequest", "TranslateResult", "TranslationProvider",
    "available_providers", "create_provider", "normalize_lang", "register",
    "MTranProvider", "OllamaProvider", "OpenAICompatibleProvider",
]
