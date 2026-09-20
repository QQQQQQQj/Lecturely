"""导出器（P1-4）：TXT / Markdown / CSV / SRT / VTT。

导出内容可选：仅原文 / 仅译文 / 双语；带 Speaker；带时间戳。
SRT/VTT 强制需要时间戳（用 segment 的 start_ms/end_ms）。
"""
from __future__ import annotations

import csv
import enum
import io
from dataclasses import dataclass
from typing import Optional

from ..utils import ms_to_mmss, ms_to_srt, ms_to_vtt


class ContentMode(str, enum.Enum):
    SOURCE = "source"            # 仅原文
    TRANSLATION = "translation"  # 仅译文
    BILINGUAL = "bilingual"      # 双语


@dataclass
class ExportOptions:
    content: ContentMode = ContentMode.BILINGUAL
    with_speaker: bool = False
    with_timestamps: bool = False


def _speaker(seg: dict, opts: ExportOptions) -> str:
    if not opts.with_speaker:
        return ""
    return (seg.get("speaker_id") or "") and f"[{seg.get('speaker_id')}] " or ""


def _lines(seg: dict, opts: ExportOptions) -> list[str]:
    src = (seg.get("source_text") or "").strip()
    tgt = (seg.get("translated_text") or "").strip()
    if opts.content == ContentMode.SOURCE:
        return [src] if src else []
    if opts.content == ContentMode.TRANSLATION:
        return [tgt] if tgt else []
    # bilingual
    out = []
    if src:
        out.append(src)
    if tgt:
        out.append(tgt)
    return out


def export_txt(segments: list[dict], opts: ExportOptions) -> str:
    parts = []
    for seg in segments:
        prefix = _speaker(seg, opts)
        ts = f"{ms_to_mmss(seg['start_ms'])} " if opts.with_timestamps else ""
        for line in _lines(seg, opts):
            parts.append(f"{prefix}{ts}{line}".rstrip())
    return "\n".join(parts) + "\n"


def export_markdown(segments: list[dict], opts: ExportOptions, *, title: str = "") -> str:
    out = []
    if title:
        out.append(f"# {title}\n")
    for seg in segments:
        speaker = _speaker(seg, opts)
        ts = f"`{ms_to_mmss(seg['start_ms'])}` " if opts.with_timestamps else ""
        lines = _lines(seg, opts)
        if not lines:
            continue
        if len(lines) == 2:
            out.append(f"{speaker}{ts}{lines[0]}  ")
            out.append(f"{lines[1]}\n")
        else:
            out.append(f"{speaker}{ts}{lines[0]}\n")
    return "\n".join(out)


def export_csv(segments: list[dict], opts: ExportOptions) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["index"]
    if opts.with_timestamps:
        header += ["start_ms", "end_ms", "start", "end"]
    if opts.with_speaker:
        header.append("speaker")
    if opts.content in (ContentMode.SOURCE, ContentMode.BILINGUAL):
        header.append("source_text")
    if opts.content in (ContentMode.TRANSLATION, ContentMode.BILINGUAL):
        header.append("translated_text")
    writer.writerow(header)
    for seg in segments:
        row = [seg["segment_index"]]
        if opts.with_timestamps:
            row += [seg["start_ms"], seg["end_ms"], ms_to_mmss(seg["start_ms"]), ms_to_mmss(seg["end_ms"])]
        if opts.with_speaker:
            row.append(seg.get("speaker_id") or "")
        if opts.content in (ContentMode.SOURCE, ContentMode.BILINGUAL):
            row.append(seg.get("source_text") or "")
        if opts.content in (ContentMode.TRANSLATION, ContentMode.BILINGUAL):
            row.append(seg.get("translated_text") or "")
        writer.writerow(row)
    return buf.getvalue()


def export_srt(segments: list[dict], opts: ExportOptions) -> str:
    blocks = []
    idx = 0
    for seg in segments:
        lines = _lines(seg, opts)
        if not lines:
            continue
        idx += 1
        speaker = _speaker(seg, opts)
        text = "\n".join(f"{speaker}{l}".rstrip() for l in lines)
        blocks.append(
            f"{idx}\n{ms_to_srt(seg['start_ms'])} --> {ms_to_srt(seg['end_ms'])}\n{text}\n"
        )
    return "\n".join(blocks)


def export_vtt(segments: list[dict], opts: ExportOptions) -> str:
    blocks = ["WEBVTT\n"]
    for seg in segments:
        lines = _lines(seg, opts)
        if not lines:
            continue
        speaker = _speaker(seg, opts)
        text = "\n".join(f"{speaker}{l}".rstrip() for l in lines)
        blocks.append(
            f"{ms_to_vtt(seg['start_ms'])} --> {ms_to_vtt(seg['end_ms'])}\n{text}\n"
        )
    return "\n".join(blocks)


FORMATS = {
    "txt": (export_txt, "text/plain; charset=utf-8", "txt"),
    "markdown": (export_markdown, "text/markdown; charset=utf-8", "md"),
    "md": (export_markdown, "text/markdown; charset=utf-8", "md"),
    "csv": (export_csv, "text/csv; charset=utf-8", "csv"),
    "srt": (export_srt, "application/x-subrip; charset=utf-8", "srt"),
    "vtt": (export_vtt, "text/vtt; charset=utf-8", "vtt"),
}


def export_segments(format: str, segments: list[dict], opts: ExportOptions,
                    *, title: str = "") -> tuple[str, str, str]:
    """返回 (内容, content_type, 扩展名)。"""
    fmt = format.lower()
    if fmt not in FORMATS:
        raise ValueError(f"不支持的导出格式：{format}")
    fn, content_type, ext = FORMATS[fmt]
    if fmt in ("markdown", "md"):
        return fn(segments, opts, title=title), content_type, ext
    return fn(segments, opts), content_type, ext
