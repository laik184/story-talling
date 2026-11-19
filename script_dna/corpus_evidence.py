"""Path/hash-checked retrieval of bounded caption evidence from any indexed source."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from corpus_index_support import CORPUS_ROOT, DEFAULT_DB
from corpus_search_support import MAX_CAPTIONS, MAX_CHARS, clip, contains_tokens
from corpus_index_support import normalize_text, word_tokens


class CorpusEvidence:
    def __init__(self, db_path: Path = DEFAULT_DB, corpus_root: Path = CORPUS_ROOT):
        self.db_path = db_path.resolve()
        self.corpus_root = corpus_root.resolve()

    def get_transcript_evidence(self, transcript_id: str,
                                caption_ids: list[str] | None = None,
                                query: str | None = None, max_captions: int = 6,
                                max_chars: int = 1200) -> dict:
        if bool(caption_ids) == bool(query and query.strip()):
            raise ValueError("Provide exactly one of caption_ids or query.")
        if not 1 <= max_captions <= MAX_CAPTIONS or not 1 <= max_chars <= MAX_CHARS:
            raise ValueError("max_captions/max_chars exceed allowed bounds.")
        if caption_ids is not None and (not caption_ids or len(caption_ids) > MAX_CAPTIONS):
            raise ValueError(f"caption_ids must contain 1–{MAX_CAPTIONS} IDs.")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM transcript WHERE transcript_id=?",
                               (transcript_id,)).fetchone()
            if not row:
                return {"ok": False, "error_type": "unknown_transcript_id",
                        "transcript_id": transcript_id}
            if row["status"] != "indexed":
                return {"ok": False, "error_type": "transcript_not_indexed",
                        "status": row["status"], "transcript_id": transcript_id}
            try:
                path = (self.corpus_root / row["relative_path"]).resolve(strict=True)
                path.relative_to(self.corpus_root)
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError) as exc:
                return {"ok": False, "error_type": "stale_or_unsafe_source",
                        "transcript_id": transcript_id, "detail": str(exc)}
            if actual != row["source_sha256"]:
                return {"ok": False, "error_type": "stale_or_unsafe_source",
                        "transcript_id": transcript_id, "expected_sha256": row["source_sha256"],
                        "actual_sha256": actual}
            if caption_ids:
                placeholders = ",".join("?" for _ in caption_ids)
                rows = conn.execute(
                    f"SELECT * FROM caption WHERE transcript_id=? AND caption_id IN ({placeholders}) ORDER BY ordinal",
                    (transcript_id, *caption_ids)).fetchall()
                if len(rows) != len(set(caption_ids)):
                    return {"ok": False, "error_type": "unknown_caption_id",
                            "transcript_id": transcript_id}
            else:
                tokens, phrase = word_tokens(query or ""), normalize_text(query or "")
                if not tokens:
                    raise ValueError("query contains no searchable letters or numbers.")
                rows = [cap for cap in conn.execute(
                    "SELECT * FROM caption WHERE transcript_id=? ORDER BY ordinal", (transcript_id,))
                    if phrase in f" {cap['normalized_text']} " or
                    contains_tokens(cap["normalized_text"], tokens)]
            output, used = [], 0
            for cap in rows[:max_captions]:
                remaining = max_chars - used
                if remaining <= 0:
                    break
                text, truncated = clip(cap["text"], min(240, remaining))
                used += len(text)
                output.append({"caption_id": cap["caption_id"], "timestamp": cap["timestamp"],
                    "timestamp_status": cap["timestamp_status"], "text": text,
                    "truncated": truncated})
            return {"ok": True, "transcript_id": transcript_id, "title": row["title"],
                    "relative_path": row["relative_path"], "source_sha256": row["source_sha256"],
                    "captions": output,
                    "parser_warnings": json.loads(row["parser_warnings_json"]),
                    "timestamp_note": "Timestamp is the source caption start; semantic end is not asserted."}
        finally:
            conn.close()
