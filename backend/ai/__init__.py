"""AI 层：AIProvider 抽象（纪要/问答/关键词），Ollama 与 OpenAI-compatible 实现。"""
from .base import (
    AIProvider, ChatMessage, ChatResult, available_ai_providers,
    create_ai_provider, register_ai,
)
# 导入 providers 以触发注册（register_ai 在模块导入时执行）
from . import providers  # noqa: F401

__all__ = [
    "AIProvider", "ChatMessage", "ChatResult", "available_ai_providers",
    "create_ai_provider", "register_ai",
]
