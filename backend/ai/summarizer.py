"""纪要生成器（P1-6 最终纪要 + P2-1 实时增量纪要）。

最终纪要产出结构化内容：标题 / 摘要 / 主要主题 / 关键知识点 / 重要结论 / 待办事项 / 关键词 / 术语。
长转录采用"分块摘要 → 合并"避免超长上下文与小模型溢出（不把全文一次性硬塞）。
回答语言与目标语言一致（中文优先）。
"""
from __future__ import annotations

from typing import Optional

from ..utils import ms_to_mmss
from .base import AIProvider, ChatMessage

FINAL_SUMMARY_INSTRUCTION = """你是一名专业的会议/课堂纪要助手。请基于下面的转录内容，生成结构化纪要。
要求：
- 使用简体中文输出
- 忠实于原文，不要编造原文没有的内容
- 严格按照以下 Markdown 结构输出（标题层级用 ##）：

## 标题
（一句话概括本次内容主题）

## 摘要
（3-5 句话概述整体内容）

## 主要主题
- （列出 2-6 个讨论的主题）

## 关键知识点
- （列出涉及的重要概念、术语、方法、事实）

## 重要结论
- （列出得出的结论或达成的共识；没有则写"无"）

## 待办事项
- （列出提到的行动项/任务；没有则写"无"）

## 关键词
（逗号分隔的 5-10 个关键词）
"""


def format_transcript(segments: list[dict], *, bilingual: bool = True,
                      with_timestamps: bool = True) -> str:
    lines = []
    for seg in segments:
        ts = f"[{ms_to_mmss(seg['start_ms'])}] " if with_timestamps else ""
        lines.append(f"{ts}{seg['source_text']}")
        if bilingual and seg.get("translated_text"):
            lines.append(f"  （{seg['translated_text']}）")
    return "\n".join(lines)


def _chunk_segments(segments: list[dict], max_chars: int = 6000) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_len = 0
    for seg in segments:
        ln = len(seg.get("source_text", "")) + 20
        if cur and cur_len + ln > max_chars:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(seg)
        cur_len += ln
    if cur:
        chunks.append(cur)
    return chunks


async def generate_final_summary(provider: AIProvider, segments: list[dict],
                                 *, target_language: str = "zh-CN") -> str:
    """生成最终结构化纪要（分块→合并）。"""
    if not segments:
        raise ValueError("没有可总结的转录内容")
    chunks = _chunk_segments(segments)
    if len(chunks) == 1:
        transcript = format_transcript(chunks[0])
        result = await provider.chat([
            ChatMessage(role="system", content=FINAL_SUMMARY_INSTRUCTION),
            ChatMessage(role="user", content=f"转录内容如下：\n\n{transcript}"),
        ], max_tokens=2048, temperature=0.3)
        return result.text

    # 多块：先逐块摘要，再合并
    partials = []
    for i, chunk in enumerate(chunks, 1):
        transcript = format_transcript(chunk)
        r = await provider.chat([
            ChatMessage(role="system", content="你是纪要助手。用简体中文，简明扼要地总结下面这段转录的要点（主题、知识点、结论、待办）。"),
            ChatMessage(role="user", content=f"第 {i}/{len(chunks)} 段转录：\n\n{transcript}"),
        ], max_tokens=800, temperature=0.3)
        partials.append(f"【第 {i} 部分】\n{r.text}")
    merged_input = "\n\n".join(partials)
    final = await provider.chat([
        ChatMessage(role="system", content=FINAL_SUMMARY_INSTRUCTION),
        ChatMessage(role="user", content=f"以下是分段要点，请整合为一份完整纪要：\n\n{merged_input}"),
    ], max_tokens=2048, temperature=0.3)
    return final.text


REALTIME_NOTE_INSTRUCTION = """你是会议纪要助手。基于"已有纪要"和"新增转录"，更新纪要（增量合并）。
要求：用简体中文；保留已有纪要中仍然有效的要点，融入新增内容；不要重复；输出更新后的完整纪要（Markdown 列表形式，分主题）。"""


async def generate_realtime_note(provider: AIProvider, *, existing_note: str,
                                 new_segments: list[dict]) -> str:
    """实时增量纪要：已有纪要 + 最近 N 分钟新增 → 合并更新（不重复发全文）。"""
    new_text = format_transcript(new_segments, bilingual=False)
    result = await provider.chat([
        ChatMessage(role="system", content=REALTIME_NOTE_INSTRUCTION),
        ChatMessage(role="user", content=(
            f"已有纪要：\n{existing_note or '（暂无）'}\n\n新增转录：\n{new_text}\n\n请输出更新后的纪要：")),
    ], max_tokens=1024, temperature=0.3)
    return result.text
