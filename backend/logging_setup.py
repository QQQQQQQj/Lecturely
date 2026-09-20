"""统一日志系统（目标文档 §15）。

级别 DEBUG/INFO/WARNING/ERROR；控制台 + 滚动文件（data/logs/lecturely.log）。
日志脱敏：不打印完整 API Key。
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_INITIALIZED = False
_KEY_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]+|(Bearer\s+[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]+")


class _RedactFilter(logging.Filter):
    """对疑似 API Key 的片段脱敏。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            redacted = _KEY_PATTERN.sub(lambda m: (m.group(1) or m.group(2) or "") + "…", msg)
            if redacted != msg:
                record.msg = redacted
                record.args = ()
        except Exception:
            pass
        return True


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(_RedactFilter())
    root.addHandler(console)

    fileh = RotatingFileHandler(
        log_dir / "lecturely.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fileh.setFormatter(fmt)
    fileh.addFilter(_RedactFilter())
    root.addHandler(fileh)

    # 降噪第三方库
    for noisy in ("httpx", "httpcore", "faster_whisper", "whisperlivekit.audio_processor"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
