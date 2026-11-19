"""Small shared helpers for lexical search and bounded result presentation."""
from __future__ import annotations

import sqlite3

from corpus_index_support import normalize_text, word_tokens

MAX_LIMIT = 50
MAX_VARIANTS = 10
MAX_QUERY = 500
MAX_CAPTIONS = 20
MAX_CHARS = 4000
EXCERPT_CHARS = 240


def phrase_expr(value: str) -> str:
    tokens = word_tokens(value)
    if not tokens:
        raise ValueError("Query contains no searchable letters or numbers.")
    return '"' + " ".join(tokens).replace('"', '""') + '"'


def keyword_expr(value: str) -> str:
    tokens = list(dict.fromkeys(word_tokens(value)))
    if not tokens:
        raise ValueError("Query contains no searchable letters or numbers.")
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def contains_tokens(text: str, tokens: list[str]) -> bool:
    padded = f" {text} "
    return all(f" {token} " in padded for token in tokens)


def clip(text: str, limit: int = EXCERPT_CHARS) -> tuple[str, bool]:
    return (text[:limit].rstrip() + "…", True) if len(text) > limit else (text, False)


def caption_result(row: sqlite3.Row, match_type: str) -> dict:
    excerpt, truncated = clip(row["text"])
    return {"caption_id": row["caption_id"], "timestamp": row["timestamp"],
            "timestamp_status": row["timestamp_status"], "excerpt": excerpt,
            "truncated": truncated, "match_type": match_type}


def caption_evidence(conn: sqlite3.Connection, transcript: sqlite3.Row,
                     tokens: list[str], phrase: str, exact: bool,
                     fts_ids: set[str]) -> list[dict]:
    rows = conn.execute("SELECT * FROM caption WHERE transcript_id=? ORDER BY ordinal",
                        (transcript["transcript_id"],)).fetchall()
    found = []
    for row in rows:
        norm = row["normalized_text"]
        match = (phrase in f" {norm} ") if exact else (
            row["caption_id"] in fts_ids or (tokens and contains_tokens(norm, tokens)))
        if match:
            found.append(caption_result(row, "exact_phrase" if exact else "caption_keyword"))
    return found[:5]


def context_captions(conn: sqlite3.Connection, transcript_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM caption WHERE transcript_id=? ORDER BY ordinal LIMIT 2",
                        (transcript_id,)).fetchall()
    return [caption_result(row, "context") for row in rows]
