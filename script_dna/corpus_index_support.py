"""Shared deterministic text normalization and SQLite index schema."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = (ROOT / "scripts").resolve()
ANNOTATION_ROOT = Path(__file__).resolve().parent / "annotations"
DEFAULT_DB = Path(__file__).resolve().parent / "corpus_index.sqlite"
SCHEMA_VERSION = "1"
FILENAME = re.compile(r"^\d{4}_(.+)\.md$", re.IGNORECASE)


def caption_id(order: int) -> str:
    return f"C{order:04d}"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    chars = [char if unicodedata.category(char)[0] in {"L", "M", "N"} else " "
             for char in value]
    return " ".join("".join(chars).split())


def word_tokens(value: str) -> list[str]:
    normalized = normalize_text(value)
    tokens, current = [], []
    for char in normalized:
        if unicodedata.category(char)[0] in {"L", "M", "N"}:
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS transcript (
      transcript_id TEXT PRIMARY KEY, relative_path TEXT UNIQUE NOT NULL,
      source_sha256 TEXT, title TEXT NOT NULL, duration_seconds INTEGER,
      status TEXT NOT NULL, normalized_title TEXT NOT NULL,
      normalized_body TEXT NOT NULL, parser_warnings_json TEXT NOT NULL,
      duplicate_group TEXT
    );
    CREATE TABLE IF NOT EXISTS caption (
      transcript_id TEXT NOT NULL, caption_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
      timestamp TEXT, timestamp_status TEXT NOT NULL, text TEXT NOT NULL,
      normalized_text TEXT NOT NULL, PRIMARY KEY (transcript_id, caption_id),
      FOREIGN KEY (transcript_id) REFERENCES transcript(transcript_id)
    );
    CREATE INDEX IF NOT EXISTS caption_order_idx ON caption(transcript_id, ordinal);
    CREATE TABLE IF NOT EXISTS narrative_tag (
      transcript_id TEXT NOT NULL, tag_text TEXT NOT NULL, normalized_tag TEXT NOT NULL,
      category TEXT, start_caption_id TEXT, end_caption_id TEXT,
      source_path TEXT NOT NULL, source_sha256 TEXT NOT NULL,
      verification_status TEXT NOT NULL,
      UNIQUE (transcript_id, normalized_tag, start_caption_id, end_caption_id)
    );
    CREATE TABLE IF NOT EXISTS index_issue (
      relative_path TEXT NOT NULL, issue_type TEXT NOT NULL, detail TEXT NOT NULL,
      source_sha256 TEXT, PRIMARY KEY (relative_path, issue_type)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
      transcript_id UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 0'
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS caption_fts USING fts5(
      transcript_id UNINDEXED, caption_id UNINDEXED, body,
      tokenize='unicode61 remove_diacritics 0'
    );
    """)
    conn.execute("INSERT OR REPLACE INTO meta VALUES('schema_version', ?)", (SCHEMA_VERSION,))


def remove_transcript(conn: sqlite3.Connection, transcript_id: str) -> None:
    conn.execute("DELETE FROM transcript_fts WHERE transcript_id=?", (transcript_id,))
    conn.execute("DELETE FROM caption_fts WHERE transcript_id=?", (transcript_id,))
    conn.execute("DELETE FROM narrative_tag WHERE transcript_id=?", (transcript_id,))
    conn.execute("DELETE FROM caption WHERE transcript_id=?", (transcript_id,))
    conn.execute("DELETE FROM transcript WHERE transcript_id=?", (transcript_id,))
