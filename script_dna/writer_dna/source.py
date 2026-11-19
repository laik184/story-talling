"""Resolve corpus records and validate their path, bytes, captions and timestamps."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from .model import DnaValidationError


class SourceVerifier:
    def __init__(self, project_root: Path | None = None):
        self.root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        self.scripts = (self.root / "scripts").resolve()
        self.corpus_db = self.root / "script_dna" / "corpus_index.sqlite"

    def validate(self, source: dict[str, Any]) -> dict[str, Any]:
        rel = PurePosixPath(str(source.get("relative_path", "")))
        if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 1:
            raise DnaValidationError("source path must be a corpus-relative transcript filename")
        path = (self.scripts / Path(*rel.parts)).resolve()
        if not path.is_relative_to(self.scripts) or not path.is_file():
            raise DnaValidationError("source transcript path is missing or outside scripts/")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != source["sha256"]:
            raise DnaValidationError("source SHA-256 mismatch; source may be stale")
        with sqlite3.connect(self.corpus_db) as db:
            db.row_factory = sqlite3.Row
            transcript = db.execute("SELECT * FROM transcript WHERE transcript_id=?",
                (source["transcript_id"],)).fetchone()
            if not transcript:
                raise DnaValidationError("transcript ID is absent from the corpus index")
            if transcript["relative_path"] != rel.as_posix() or transcript["source_sha256"] != digest:
                raise DnaValidationError("corpus index path/hash does not match the source file")
            captions = []
            for caption_id, timestamp in zip(source["caption_ids"], source["timestamps"]):
                row = db.execute("SELECT caption_id,timestamp,timestamp_status,text FROM caption "
                    "WHERE transcript_id=? AND caption_id=?", (source["transcript_id"], caption_id)).fetchone()
                if not row:
                    raise DnaValidationError(f"caption ID not found: {caption_id}")
                if row["timestamp"] != timestamp:
                    raise DnaValidationError(f"caption timestamp mismatch: {caption_id}")
                captions.append(dict(row))
        joined = " ".join(row["text"] for row in captions)
        if source["excerpt"] not in joined:
            raise DnaValidationError("source excerpt does not occur in the cited caption span")
        return {"relative_path": rel.as_posix(), "sha256": digest,
                "captions": captions, "title": transcript["title"],
                "duration_seconds": transcript["duration_seconds"]}
