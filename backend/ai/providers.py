"""Ollama 与 OpenAI-compatible AI Provider 实现。"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..errors import ProviderError
from ..translation.base import ProviderHealth
from .base import AIProvider, ChatMessage, ChatResult, register_ai


class OllamaAIProvider:
    name = "ollama"

    def __init__(self, config: dict[str, Any]):
        self._endpoint = (config.get("endpoint") or "http://127.0.0.1:11434").rstrip("/")
        self._model = config.get("model") or "qwen2.5:1.5b"
        self._timeout = float(config.get("timeout", 120))

    async def chat(self, messages: list[ChatMessage], *, timeout: float = 120,
                   max_tokens: int = 2048, temperature: float = 0.3) -> ChatResult:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self._endpoint}/api/chat", json=payload)
            if resp.status_code == 404:
                raise ProviderError("ai", "ollama_model_missing",
                                    f"Ollama 模型未安装：{self._model}",
                                    suggestion=f"请执行 ollama pull {self._model}")
            if resp.status_code != 200:
                raise ProviderError("ai", "ollama_http_error",
                                    f"Ollama 返回错误（{resp.status_code}）", retryable=True)
            text = (resp.json().get("message") or {}).get("content", "")
            if not text.strip():
                raise ProviderError("ai", "ollama_empty", "AI 返回空结果", retryable=True)
            return ChatResult(text=text.strip(), provider=self.name, model=self._model)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as e:
            raise ProviderError("ai", "ollama_unreachable",
                                "Ollama 服务不可达",
                                suggestion="请启动 Ollama，或在设置页切换 AI 服务", retryable=True) from e

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._endpoint}/api/tags")
            if resp.status_code == 200:
                return [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception:
            pass
        return []

    async def health(self) -> ProviderHealth:
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._endpoint}/api/tags")
            if resp.status_code == 200:
                models = await self.list_models()
                has = any(self._model.split(":")[0] in m for m in models)
                return ProviderHealth(ok=True, latency_ms=round((time.time() - t0) * 1000, 1),
                                      message=f"Ollama 运行中（{self._model}{'已安装' if has else '未安装'}）")
            return ProviderHealth(ok=False, message=f"HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(ok=False, message=f"Ollama 不可达：{e}")


class OpenAICompatibleAIProvider:
    name = "openai_compatible"

    def __init__(self, config: dict[str, Any]):
        self._base_url = (config.get("base_url") or "").rstrip("/")
        self._api_key = config.get("api_key") or ""
        self._model = config.get("model") or ""
        self._timeout = float(config.get("timeout", 120))

    async def chat(self, messages: list[ChatMessage], *, timeout: float = 120,
                   max_tokens: int = 2048, temperature: float = 0.3) -> ChatResult:
        if not self._base_url or not self._model:
            raise ProviderError("ai", "openai_not_configured",
                                "在线 AI 未配置（缺少 Base URL 或 Model）",
                                suggestion="请在设置页填写在线 AI 的 Base URL / API Key / Model")
        payload = {"model": self._model,
                   "messages": [{"role": m.role, "content": m.content} for m in messages],
                   "max_tokens": max_tokens, "temperature": temperature, "stream": False}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self._base_url}/chat/completions", json=payload, headers=headers)
            if resp.status_code == 401:
                raise ProviderError("ai", "openai_auth_failed", "API Key 无效",
                                    suggestion="请检查设置页的 API Key")
            if resp.status_code != 200:
                raise ProviderError("ai", "openai_http_error",
                                    f"在线 AI 返回错误（{resp.status_code}）", retryable=True)
            text = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
            if not text.strip():
                raise ProviderError("ai", "openai_empty", "AI 返回空结果", retryable=True)
            return ChatResult(text=text.strip(), provider=self.name, model=self._model)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as e:
            raise ProviderError("ai", "openai_unreachable", "在线 AI 不可达或超时",
                                suggestion="请检查网络与 Base URL", retryable=True) from e

    async def list_models(self) -> list[str]:
        return []

    async def health(self) -> ProviderHealth:
        if not self._base_url:
            return ProviderHealth(ok=False, message="未配置 Base URL")
        t0 = time.time()
        try:
            headers = {"Authorization": f"Bearer {self._api_key}"}
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(f"{self._base_url}/models", headers=headers)
            ok = resp.status_code in (200, 401)
            return ProviderHealth(ok=ok, latency_ms=round((time.time() - t0) * 1000, 1),
                                  message=f"端点可达（HTTP {resp.status_code}）" if ok else f"HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(ok=False, message=f"端点不可达：{e}")


register_ai(OllamaAIProvider.name, OllamaAIProvider)
register_ai(OpenAICompatibleAIProvider.name, OpenAICompatibleAIProvider)
