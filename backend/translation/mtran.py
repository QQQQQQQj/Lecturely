"""MTranServer 本地翻译 sidecar 客户端（默认本地 Provider，ADR-004）。

MTranServer 以独立进程运行（Apache-2.0），HTTP API：
  POST {endpoint}/translate  {"from": "...", "to": "...", "text": "..."} → {"result": "..."}
崩溃不拖垮主进程；语言码需归一化（zh-CN → zh-Hans）。
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from ..errors import ProviderError
from ..logging_setup import get_logger
from .base import ProviderHealth, TranslateRequest, TranslateResult, normalize_lang

logger = get_logger(__name__)


class MTranProvider:
    name = "mtran"

    def __init__(self, config: dict[str, Any]):
        self._endpoint = (config.get("endpoint") or "http://127.0.0.1:8989").rstrip("/")
        self._timeout = float(config.get("timeout", 8))
        self._retries = int(config.get("retries", 1))

    def supported_languages(self) -> Optional[list[str]]:
        return None  # 由服务端 /languages 决定，此处不限制

    def supports_context(self) -> bool:
        return False  # mtran 不支持上下文，只送当前句

    async def translate(self, req: TranslateRequest) -> TranslateResult:
        payload = {
            "from": normalize_lang(req.source_lang) if req.source_lang != "auto" else "auto",
            "to": normalize_lang(req.target_lang),
            "text": req.text,
        }
        last_err: Optional[Exception] = None
        for attempt in range(self._retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(f"{self._endpoint}/translate", json=payload)
                if resp.status_code != 200:
                    raise ProviderError("translation", "mtran_http_error",
                                        f"本地翻译服务返回错误（{resp.status_code}）",
                                        suggestion="请确认 MTranServer 正在运行", retryable=True)
                data = resp.json()
                text = data.get("result") or data.get("text") or ""
                if not text.strip():
                    raise ProviderError("translation", "mtran_empty",
                                        "本地翻译服务返回空结果", retryable=True)
                return TranslateResult(text=text.strip(), provider=self.name)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_err = e
                logger.debug("mtran 连接失败（尝试 %d）：%s", attempt + 1, e)
            except httpx.TimeoutException as e:
                last_err = e
                logger.debug("mtran 超时（尝试 %d）：%s", attempt + 1, e)
            except ProviderError:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.debug("mtran 异常（尝试 %d）：%s", attempt + 1, e)
        raise ProviderError("translation", "mtran_unreachable",
                            "本地翻译服务不可用（MTranServer 未运行或不可达）",
                            suggestion="请启动 MTranServer，或在设置页切换翻译服务（Ollama / 在线 API）",
                            retryable=True)

    async def health(self) -> ProviderHealth:
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._endpoint}/health")
            ok = resp.status_code == 200
            return ProviderHealth(ok=ok, latency_ms=round((time.time() - t0) * 1000, 1),
                                  message="MTranServer 运行中" if ok else f"HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(ok=False, message=f"MTranServer 不可达：{e}")
