"""统一错误契约（目标文档 §14）。

所有 Provider / 采集 / 业务层抛出结构化错误，前端据此展示
"发生了什么 / 可能原因 / 建议操作 / 是否可重试"，而非裸 Error 500。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ErrorInfo:
    scope: str                 # asr | translation | recording | db | audio | upload | ai | config | session
    code: str                  # 机器可读错误码
    message: str               # 发生了什么（用户可读）
    suggestion: str = ""       # 建议操作
    retryable: bool = False    # 是否可重试
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "retryable": self.retryable,
            "detail": self.detail,
        }


class LecturelyError(Exception):
    """业务层统一异常。"""

    def __init__(self, info: ErrorInfo):
        super().__init__(info.message)
        self.info = info

    def to_dict(self) -> dict[str, Any]:
        return self.info.to_dict()


class ProviderError(LecturelyError):
    """Provider（ASR/翻译/AI）调用失败。"""

    def __init__(self, scope: str, code: str, message: str,
                 suggestion: str = "", retryable: bool = False, detail: Optional[dict] = None):
        super().__init__(ErrorInfo(scope, code, message, suggestion, retryable, detail or {}))


class AudioSourceError(LecturelyError):
    """音频采集失败。"""

    def __init__(self, code: str, message: str, suggestion: str = "", retryable: bool = False):
        super().__init__(ErrorInfo("audio", code, message, suggestion, retryable))


def audio_error(code: str, message: str, suggestion: str = "", retryable: bool = False) -> AudioSourceError:
    return AudioSourceError(code, message, suggestion, retryable)
