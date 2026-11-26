"""SQLite persistence for the corpus, provenance, and workflow sessions."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS transcripts (
    transcript_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    title TEXT,
    creator TEXT,
    published TEXT,
    duration_seconds INTEGER,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    rank INTEGER,
    transcript_status TEXT,
    language_track TEXT,
    metadata_json TEXT NOT NULL,
    raw_markdown TEXT NOT NULL,
    transcript_text TEXT NOT NULL,
    analysis_text TEXT,
    content_hash TEXT NOT NULL,
    mtime_ns INTEGER,
    file_size INTEGER,
    file_status TEXT NOT NULL DEFAULT 'available',
    parsing_status TEXT NOT NULL DEFAULT 'warning',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    caption_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transcripts_published ON transcripts(published);
CREATE INDEX IF NOT EXISTS idx_transcripts_creator ON transcripts(creator);
CREATE INDEX IF NOT EXISTS idx_transcripts_file_status ON transcripts(file_status);
CREATE TABLE IF NOT EXISTS captions (
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    time_seconds REAL NOT NULL,
    text TEXT NOT NULL,
    section TEXT,
    source_line INTEGER NOT NULL,
    PRIMARY KEY(transcript_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_captions_time ON captions(transcript_id, time_seconds);
CREATE TABLE IF NOT EXISTS index_issues (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_type TEXT NOT NULL,
    relative_path TEXT,
    details TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
    transcript_id UNINDEXED, title, filename, metadata_text, transcript_text, analysis_text,
    tokenize='unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS caption_fts USING fts5(
    transcript_id UNINDEXED, ordinal UNINDEXED, timestamp UNINDEXED, section UNINDEXED, text,
    tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS instruction_versions (
    sha256 TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    content TEXT NOT NULL,
    source_file TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    operation TEXT NOT NULL,
    format TEXT,
    target_duration_seconds INTEGER,
    constraints_json TEXT NOT NULL,
    instruction_sha256 TEXT NOT NULL,
    instruction_version TEXT NOT NULL,
    stage TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_context (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    context_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(session_id, context_key)
);
CREATE TABLE IF NOT EXISTS session_references (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    discovered_at TEXT,
    inspected_at TEXT,
    last_query TEXT,
    PRIMARY KEY(session_id, transcript_id)
);
CREATE TABLE IF NOT EXISTS session_read_ranges (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    PRIMARY KEY(session_id, transcript_id, start_offset, end_offset)
);
CREATE TABLE IF NOT EXISTS reference_analyses (
    analysis_id TEXT PRIMARY KEY,
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    session_id TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    analysis_json TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    semantic_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS language_patterns (
    pattern_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES reference_analyses(analysis_id) ON DELETE CASCADE,
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    session_id TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    category TEXT NOT NULL,
    pattern TEXT NOT NULL,
    function TEXT NOT NULL,
    explanation TEXT,
    evidence_quote TEXT NOT NULL,
    timestamp TEXT,
    source_line INTEGER,
    learning_level TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_language_patterns_category ON language_patterns(category, evidence_status);
CREATE TABLE IF NOT EXISTS corpus_learning_state (
    transcript_id TEXT PRIMARY KEY REFERENCES transcripts(transcript_id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','inspected','analyzed','evidence_validated','failed','requires_review')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_batch_id TEXT,
    last_error_json TEXT,
    updated_at TEXT NOT NULL,
    analyzed_at TEXT,
    evidence_validated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_corpus_learning_status ON corpus_learning_state(status, updated_at);
CREATE TABLE IF NOT EXISTS corpus_learning_batches (
    batch_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK(purpose IN ('outline','script')),
    topic TEXT NOT NULL,
    instruction_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','paused','completed','requires_review','failed')),
    requested_items INTEGER NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    consumed_at TEXT
);
CREATE TABLE IF NOT EXISTS corpus_learning_batch_items (
    batch_id TEXT NOT NULL REFERENCES corpus_learning_batches(batch_id) ON DELETE CASCADE,
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','inspected','analyzed','evidence_validated','failed','requires_review')),
    attempts INTEGER NOT NULL DEFAULT 0,
    delivery_count INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT,
    ai_inspected_at TEXT,
    analysis_submitted_at TEXT,
    evidence_validated_at TEXT,
    error_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(batch_id, transcript_id),
    UNIQUE(batch_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_corpus_learning_batch_items_status ON corpus_learning_batch_items(batch_id,status,sequence_no);
CREATE TABLE IF NOT EXISTS corpus_learning_analyses (
    analysis_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES corpus_learning_batches(batch_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    content_hash TEXT NOT NULL,
    summary TEXT NOT NULL,
    existing_dna_status TEXT NOT NULL,
    existing_dna_review_json TEXT NOT NULL,
    topic_specific_analysis_json TEXT NOT NULL,
    ai_reported_inspected INTEGER NOT NULL,
    validation_status TEXT NOT NULL DEFAULT 'pending',
    validation_error_json TEXT,
    semantic_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corpus_learning_patterns (
    pattern_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES corpus_learning_analyses(analysis_id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL REFERENCES corpus_learning_batches(batch_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    transcript_id TEXT NOT NULL REFERENCES transcripts(transcript_id),
    content_hash TEXT NOT NULL,
    bank_category TEXT NOT NULL,
    script_dna_dimension TEXT,
    pattern TEXT NOT NULL,
    reference_does TEXT NOT NULL,
    why_works TEXT NOT NULL,
    principle TEXT NOT NULL,
    function TEXT NOT NULL,
    suitable_applications_json TEXT NOT NULL,
    avoid_applications_json TEXT NOT NULL,
    original_equivalent_method TEXT NOT NULL,
    evidence_quotes_json TEXT NOT NULL,
    learning_level TEXT NOT NULL,
    source_topic TEXT,
    target_topic TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    semantic_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corpus_learning_patterns_category ON corpus_learning_patterns(bank_category,validation_status);
CREATE VIRTUAL TABLE IF NOT EXISTS corpus_learning_fts USING fts5(
    pattern_id UNINDEXED, transcript_id UNINDEXED, bank_category UNINDEXED, script_dna_dimension,
    pattern, reference_does, why_works, principle, function, suitable_applications, avoid_applications,
    original_equivalent_method, source_topic, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS outlines (
    outline_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    corpus_learning_batch_id TEXT,
    content TEXT NOT NULL,
    references_json TEXT NOT NULL,
    duration_estimate_seconds INTEGER,
    word_count INTEGER,
    fallback_reason TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_outlines_session ON outlines(session_id, status);
CREATE TABLE IF NOT EXISTS scripts (
    script_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    corpus_learning_batch_id TEXT,
    outline_id TEXT REFERENCES outlines(outline_id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    references_json TEXT NOT NULL,
    mappings_json TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    estimated_duration_seconds REAL NOT NULL,
    speaking_rate_wpm INTEGER NOT NULL,
    pause_allowance_percent REAL NOT NULL,
    instruction_sha256 TEXT NOT NULL,
    fallback_reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scripts_session ON scripts(session_id);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level="DEFERRED")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            self._ensure_column(connection, "outlines", "fallback_reason", "TEXT")
            self._ensure_column(connection, "outlines", "corpus_learning_batch_id", "TEXT")
            self._ensure_column(connection, "scripts", "fallback_reason", "TEXT")
            self._ensure_column(connection, "scripts", "corpus_learning_batch_id", "TEXT")

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, name: str, declaration: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
