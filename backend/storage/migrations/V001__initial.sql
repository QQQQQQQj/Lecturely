-- V001 初始 schema（对应 04-database-schema.md）
CREATE TABLE IF NOT EXISTS folders (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id              TEXT PRIMARY KEY,
  folder_id       TEXT REFERENCES folders(id) ON DELETE SET NULL,
  title           TEXT NOT NULL,
  source_language TEXT NOT NULL,
  target_language TEXT NOT NULL,
  input_mode      TEXT NOT NULL,
  status          TEXT NOT NULL,
  started_at      INTEGER,
  ended_at        INTEGER,
  duration_ms     INTEGER NOT NULL DEFAULT 0,
  recording_id    TEXT,
  segment_count   INTEGER NOT NULL DEFAULT 0,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_folder ON sessions(folder_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS transcript_segments (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  segment_index   INTEGER NOT NULL,
  speaker_id      TEXT,
  start_ms        INTEGER NOT NULL,
  end_ms          INTEGER NOT NULL,
  source_language TEXT NOT NULL,
  target_language TEXT NOT NULL,
  source_text     TEXT NOT NULL,
  translated_text TEXT,
  confidence      REAL,
  status          TEXT NOT NULL DEFAULT 'committed',
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL,
  UNIQUE(session_id, segment_index)
);
CREATE INDEX IF NOT EXISTS idx_segments_session ON transcript_segments(session_id, segment_index);
CREATE INDEX IF NOT EXISTS idx_segments_source_text ON transcript_segments(source_text);

CREATE TABLE IF NOT EXISTS recordings (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  file_path   TEXT NOT NULL,
  format      TEXT NOT NULL DEFAULT 'wav',
  sample_rate INTEGER NOT NULL DEFAULT 16000,
  channels    INTEGER NOT NULL DEFAULT 1,
  size_bytes  INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  type        TEXT NOT NULL,
  content     TEXT NOT NULL,
  provider    TEXT,
  model       TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summaries_session ON summaries(session_id, type);

CREATE TABLE IF NOT EXISTS ai_messages (
  id             TEXT PRIMARY KEY,
  session_id     TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role           TEXT NOT NULL,
  content        TEXT NOT NULL,
  citations_json TEXT,
  created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_messages_session ON ai_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS settings (
  key         TEXT PRIMARY KEY,
  value_json  TEXT NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS speakers (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  label       TEXT NOT NULL,
  display_name TEXT,
  created_at  INTEGER NOT NULL
);

-- 全文搜索（FTS5 外置内容模式）
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
  source_text, translated_text,
  content='transcript_segments', content_rowid='rowid'
);
