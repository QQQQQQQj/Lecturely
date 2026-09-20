"""OpenAI-compatible 在线翻译 Provider（ADR-004）。

支持任意 OpenAI 兼容端点（可配 base_url/api_key/model/timeout），不硬编码供应商。
使用 chat/completions，上下文 few-shot 提升连贯性。
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


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, config: dict[str, Any]):
        self._base_url = (config.get("base_url") or "").rstrip("/")
        self._api_key = config.get("api_key") or ""
        self._model = config.get("model") or ""
        self._timeout = float(config.get("timeout", 30))
        self._max_tokens = int(config.get("max_tokens", 512))

    def supported_languages(self) -> Optional[list[str]]:
        return None

    def supports_context(self) -> bool:
        return True

    def _check_config(self) -> None:
        if not self._base_url or not self._model:
            raise ProviderError("translation", "openai_not_configured",
                                "在线翻译未配置（缺少 Base URL 或 Model）",
                                suggestion="请在设置页填写在线翻译的 Base URL / API Key / Model")
        if not self._api_key:
            raise ProviderError("translation", "openai_key_missing",
                                "在线翻译未配置 API Key",
                                suggestion="请在设置页填写 API Key")

    async def translate(self, req: TranslateRequest) -> TranslateResult:
        self._check_config()
        messages = build_translate_messages(req.text, req.target_lang, req.context, req.source_lang)
        payload = {"model": self._model, "messages": messages,
                   "max_tokens": self._max_tokens, "temperature": 0.2, "stream": False}
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        url = f"{self._base_url}/chat/completions" if not self._base_url.endswith("/v1") \
            else f"{self._base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 401:
                raise ProviderError("translation", "openai_auth_failed",
                                    "API Key 无效或已过期", suggestion="请检查设置页的 API Key")
            if resp.status_code == 429:
                raise ProviderError("translation", "openai_rate_limited",
                                    "在线翻译请求受限（429）", suggestion="稍后重试或更换模型", retryable=True)
            if resp.status_code != 200:
                raise ProviderError("translation", "openai_http_error",
                                    f"在线翻译服务返回错误（{resp.status_code}）", retryable=True)
            data = resp.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if not text.strip():
                raise ProviderError("translation", "openai_empty", "在线翻译返回空结果", retryable=True)
            return TranslateResult(text=text.strip(), provider=self.name)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as e:
            raise ProviderError("translation", "openai_timeout",
                                "在线翻译请求超时或不可达", suggestion="请检查网络与 Base URL", retryable=True) from e

    async def health(self) -> ProviderHealth:
        if not self._base_url:
            return ProviderHealth(ok=False, message="未配置 Base URL")
        t0 = time.time()
        try:
            headers = {"Authorization": f"Bearer {self._api_key}"}
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(f"{self._base_url}/models", headers=headers)
            ok = resp.status_code in (200, 401)  # 401 也证明端点可达
            return ProviderHealth(ok=ok, latency_ms=round((time.time() - t0) * 1000, 1),
                                  message=f"端点可达（HTTP {resp.status_code}）" if ok else f"HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(ok=False, message=f"端点不可达：{e}")
