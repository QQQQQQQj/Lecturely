"""翻译上下文构造（借鉴 LiveCaptions-Translator 的 few-shot 上下文策略）。

LLM 系翻译把最近 N 句构造成 user/assistant 消息对，提升上下文连贯性。
"""
from __future__ import annotations

from collections import deque
from typing import Optional

DEFAULT_CONTEXT_TURNS = 2


class TranslationContext:
    """维护最近 N 句 (原文, 译文) 对，供 LLM 翻译 few-shot。"""

    def __init__(self, max_turns: int = DEFAULT_CONTEXT_TURNS):
        self._turns: deque[tuple[str, str]] = deque(maxlen=max_turns)

    def add(self, source: str, translation: str) -> None:
        if source and translation:
            self._turns.append((source, translation))

    def get(self) -> list[tuple[str, str]]:
        return list(self._turns)

    def clear(self) -> None:
        self._turns.clear()


_LANG_NAME = {
    "zh-CN": "简体中文", "zh-TW": "繁体中文", "zh": "中文", "en": "English",
    "ja": "日本語", "ko": "한국어", "fr": "Français", "de": "Deutsch",
    "es": "Español", "ru": "Русский",
}


def lang_name(code: str) -> str:
    return _LANG_NAME.get(code, code)


def build_translate_messages(source_text: str, target_lang: str,
                             context: Optional[list[tuple[str, str]]] = None,
                             source_lang: str = "auto") -> list[dict[str, str]]:
    """构造 chat 消息：system（角色与要求）+ 上下文 few-shot + 当前句。

    实测（qwen2.5:1.5b）：小模型对纯 system 指令遵循弱，短句会直接回显原文；
    必须在每条 user 消息内联翻译指令才能稳定输出目标语言。
    """
    target_name = lang_name(target_lang)
    system = (
        f"你是专业同声传译引擎。把用户输入的句子翻译成{target_name}。"
        "只输出译文本身，不要解释、不要加引号、不要保留原文、不要输出任何额外内容。"
        "保持专业术语准确、语句通顺自然。"
    )
    instr = f"把下面这句话翻译成{target_name}，只输出译文：\n"
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for src, tgt in (context or [])[-DEFAULT_CONTEXT_TURNS:]:
        messages.append({"role": "user", "content": instr + src})
        messages.append({"role": "assistant", "content": tgt})
    messages.append({"role": "user", "content": instr + source_text})
    return messages
