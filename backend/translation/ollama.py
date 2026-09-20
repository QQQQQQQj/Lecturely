"""Ollama 本地 LLM 翻译 Provider（免 key，ADR-004）。

POST {endpoint}/api/chat，本地开源模型翻译，支持上下文 few-shot。
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from ..errors import ProviderError
from ..logging_setup import get_logger
from .base import ProviderHealth, TranslateRequest, TranslateResult
from .context import build_translate_messages

logger = get_logger(__name__)


class OllamaProvider:
    name = "ollama"

    def __init__(self, config: dict[str, Any]):
        self._endpoint = (config.get("endpoint") or "http://127.0.0.1:11434").rstrip("/")
        self._model = config.get("model") or "qwen2.5:3b"
        self._timeout = float(config.get("timeout", 60))

    def supported_languages(self) -> Optional[list[str]]:
        return None

    def supports_context(self) -> bool:
        return True

    async def translate(self, req: TranslateRequest) -> TranslateResult:
        messages = build_translate_messages(req.text, req.target_lang, req.context, req.source_lang)
        payload = {"model": self._model, "messages": messages, "stream": False,
                   "options": {"temperature": 0.2}}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._endpoint}/api/chat", json=payload)
            if resp.status_code == 404:
                raise ProviderError("translation", "ollama_model_missing",
                                    f"Ollama 模型未安装：{self._model}",
                                    suggestion=f"请先在终端执行 ollama pull {self._model}")
            if resp.status_code != 200:
                raise ProviderError("translation", "ollama_http_error",
                                    f"Ollama 返回错误（{resp.status_code}）", retryable=True)
            data = resp.json()
            text = (data.get("message") or {}).get("content", "")
            if not text.strip():
                raise ProviderError("translation", "ollama_empty", "Ollama 返回空结果", retryable=True)
            return TranslateResult(text=text.strip(), provider=self.name)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as e:
            raise ProviderError("translation", "ollama_unreachable",
                                "Ollama 服务不可达",
                                suggestion="请启动 Ollama，或在设置页切换翻译服务", retryable=True) from e

    async def health(self) -> ProviderHealth:
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._endpoint}/api/tags")
            if resp.status_code == 200:
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                has_model = any(self._model.split(":")[0] in m for m in models)
                msg = f"Ollama 运行中（模型 {self._model}{'已安装' if has_model else '未安装，需 ollama pull'}）"
                return ProviderHealth(ok=True, latency_ms=round((time.time() - t0) * 1000, 1), message=msg)
            return ProviderHealth(ok=False, message=f"HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(ok=False, message=f"Ollama 不可达：{e}")
