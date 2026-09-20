"""会话内 AI 问答（P2-2，RAG）。

策略：
- 短会话：全部转录直接入上下文。
- 长会话：用 bge-m3 嵌入检索 top-K 相关段（跨语言有效，中文问题可检索英文转录），再作答。
- 回答优先基于当前 Session；返回 {答案, 引用段(segment_id/start_ms/snippet)}。
- 嵌入按会话缓存，避免重复计算。
"""
from __future__ import annotations

import math
from typing import Any, Optional

import httpx

from ..logging_setup import get_logger
from ..utils import ms_to_mmss
from .base import AIProvider, ChatMessage

logger = get_logger(__name__)

TOP_K = 6
STUFF_THRESHOLD = 25  # 少于该段数直接全文入上下文

# 每会话段嵌入缓存：{session_id: {"embeddings": [[...]], "segment_ids": [...], "model": str}}
_embedding_cache: dict[str, dict[str, Any]] = {}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class Embedder:
    """Ollama 嵌入客户端（bge-m3 等）。"""

    def __init__(self, endpoint: str = "http://127.0.0.1:11434", model: str = "bge-m3"):
        self._endpoint = endpoint.rstrip("/")
        self._model = model

    async def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{self._endpoint}/api/embed",
                                         json={"model": self._model, "input": texts})
            if resp.status_code != 200:
                logger.warning("嵌入请求失败（%s）", resp.status_code)
                return None
            return resp.json().get("embeddings")
        except Exception as e:  # noqa: BLE001
            logger.warning("嵌入不可用：%s", e)
            return None


def _segment_text(seg: dict) -> str:
    text = seg.get("source_text", "")
    if seg.get("translated_text"):
        text += " " + seg["translated_text"]
    return text


async def _ensure_embeddings(session_id: str, segments: list[dict],
                             embedder: Embedder) -> Optional[list[list[float]]]:
    cached = _embedding_cache.get(session_id)
    seg_ids = [s["id"] for s in segments]
    if cached and cached.get("segment_ids") == seg_ids:
        return cached["embeddings"]
    texts = [_segment_text(s) for s in segments]
    embeddings = await embedder.embed(texts)
    if embeddings is not None:
        _embedding_cache[session_id] = {
            "segment_ids": seg_ids, "embeddings": embeddings, "model": embedder._model,
        }
    return embeddings


def retrieve(segments: list[dict], query_embedding: list[float],
             embeddings: list[list[float]], top_k: int = TOP_K) -> list[dict]:
    scored = []
    for seg, emb in zip(segments, embeddings):
        scored.append((_cosine(query_embedding, emb), seg))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [seg for _, seg in scored[:top_k]]


QA_INSTRUCTION = """你是本次会话的智能问答助手。请基于给定的转录片段（含时间戳）回答用户问题。
要求：
- 用与用户问题相同的语言回答（中文问题用中文，英文问题用英文）
- 回答必须严格基于给定片段，不要编造片段中没有的信息
- 如果片段中没有相关信息，请明确说明"根据本次会话内容，没有找到相关信息"
- 回答后请在末尾列出你依据的片段时间戳（格式：[MM:SS]）"""


async def answer_question(*, provider: AIProvider, embedder: Embedder,
                          session_id: str, segments: list[dict], question: str,
                          history: Optional[list[dict]] = None) -> dict:
    """回答关于当前会话的问题，返回 {answer, citations}。"""
    if not segments:
        return {"answer": "该会话还没有转录内容，无法回答。", "citations": []}

    # 1. 检索相关段（短会话全量，长会话 RAG）
    if len(segments) <= STUFF_THRESHOLD:
        relevant = segments
    else:
        embeddings = await _ensure_embeddings(session_id, segments, embedder)
        q_emb = await embedder.embed([question])
        if embeddings and q_emb:
            relevant = retrieve(segments, q_emb[0], embeddings)
            relevant = sorted(relevant, key=lambda s: s["start_ms"])  # 按时间排序
        else:
            relevant = segments[-STUFF_THRESHOLD:]  # 嵌入不可用时退化：最近段

    # 2. 构造上下文
    context_lines = []
    for seg in relevant:
        line = f"[{ms_to_mmss(seg['start_ms'])}] {seg['source_text']}"
        if seg.get("translated_text"):
            line += f"（{seg['translated_text']}）"
        context_lines.append(line)
    context = "\n".join(context_lines)

    # 3. 生成回答（含最近几轮对话历史）
    messages: list[ChatMessage] = [ChatMessage(role="system", content=QA_INSTRUCTION)]
    for h in (history or [])[-4:]:
        messages.append(ChatMessage(role=h["role"], content=h["content"]))
    messages.append(ChatMessage(role="user", content=(
        f"转录片段：\n{context}\n\n问题：{question}")))

    result = await provider.chat(messages, max_tokens=1024, temperature=0.3)

    # 4. 引用（返回检索到的段作为来源）
    citations = [
        {
            "segment_id": seg["id"],
            "segment_index": seg["segment_index"],
            "start_ms": seg["start_ms"],
            "timestamp": ms_to_mmss(seg["start_ms"]),
            "text": (seg.get("source_text") or "")[:120],
        }
        for seg in relevant[:4]
    ]
    return {"answer": result.text, "citations": citations}


def invalidate_cache(session_id: str) -> None:
    _embedding_cache.pop(session_id, None)
