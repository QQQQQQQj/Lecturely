"""数据表定义（SQLAlchemy Core）。

对应 docs/architecture/04-database-schema.md。仅用标准类型，保留 PostgreSQL 迁移可能。
时间戳约定：业务时间戳 = epoch ms（BIGINT）；segment 起止 = 录音相对毫秒（INT）。
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Column, Float, ForeignKey, Index, Integer, MetaData, Table, Text, UniqueConstraint,
)

metadata = MetaData()

folders = Table(
    "folders", metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

sessions = Table(
    "sessions", metadata,
    Column("id", Text, primary_key=True),
    Column("folder_id", Text, ForeignKey("folders.id", ondelete="SET NULL"), nullable=True),
    Column("title", Text, nullable=False),
    Column("source_language", Text, nullable=False),
    Column("target_language", Text, nullable=False),
    Column("input_mode", Text, nullable=False),          # mic | system | mixed | file
    Column("status", Text, nullable=False),              # idle|recording|paused|processing|completed|failed|recovered
    Column("started_at", BigInteger, nullable=True),
    Column("ended_at", BigInteger, nullable=True),
    Column("duration_ms", BigInteger, nullable=False, server_default="0"),
    Column("recording_id", Text, nullable=True),
    Column("segment_count", Integer, nullable=False, server_default="0"),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    Index("idx_sessions_folder", "folder_id"),
    Index("idx_sessions_started", "started_at"),
    Index("idx_sessions_status", "status"),
)

transcript_segments = Table(
    "transcript_segments", metadata,
    Column("id", Text, primary_key=True),
    Column("session_id", Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("segment_index", Integer, nullable=False),
    Column("speaker_id", Text, nullable=True),
    Column("start_ms", Integer, nullable=False),
    Column("end_ms", Integer, nullable=False),
    Column("source_language", Text, nullable=False),
    Column("target_language", Text, nullable=False),
    Column("source_text", Text, nullable=False),
    Column("translated_text", Text, nullable=True),
    Column("confidence", Float, nullable=True),
    Column("status", Text, nullable=False, server_default="committed"),  # committed|translated|translation_failed
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint("session_id", "segment_index", name="uq_segment_session_index"),
    Index("idx_segments_session", "session_id", "segment_index"),
)

recordings = Table(
    "recordings", metadata,
    Column("id", Text, primary_key=True),
    Column("session_id", Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("file_path", Text, nullable=False),
    Column("format", Text, nullable=False, server_default="wav"),
    Column("sample_rate", Integer, nullable=False, server_default="16000"),
    Column("channels", Integer, nullable=False, server_default="1"),
    Column("size_bytes", BigInteger, nullable=False, server_default="0"),
    Column("duration_ms", BigInteger, nullable=False, server_default="0"),
    Column("created_at", BigInteger, nullable=False),
)

summaries = Table(
    "summaries", metadata,
    Column("id", Text, primary_key=True),
    Column("session_id", Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("type", Text, nullable=False),                # realtime | final | custom
    Column("content", Text, nullable=False),
    Column("provider", Text, nullable=True),
    Column("model", Text, nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    Index("idx_summaries_session", "session_id", "type"),
)

ai_messages = Table(
    "ai_messages", metadata,
    Column("id", Text, primary_key=True),
    Column("session_id", Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("role", Text, nullable=False),                # user | assistant
    Column("content", Text, nullable=False),
    Column("citations_json", Text, nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Index("idx_ai_messages_session", "session_id", "created_at"),
)

settings = Table(
    "settings", metadata,
    Column("key", Text, primary_key=True),
    Column("value_json", Text, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
)

speakers = Table(
    "speakers", metadata,
    Column("id", Text, primary_key=True),
    Column("session_id", Text, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("label", Text, nullable=False),
    Column("display_name", Text, nullable=True),
    Column("created_at", BigInteger, nullable=False),
)

schema_migrations = Table(
    "schema_migrations", metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", BigInteger, nullable=False),
)
