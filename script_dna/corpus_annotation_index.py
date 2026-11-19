"""Import existing, explicitly unreviewed narrative labels into the search index."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from corpus_index_support import ANNOTATION_ROOT, ROOT, caption_id, normalize_text, sha256


def index_existing_annotations(conn: sqlite3.Connection,
                               issues: list[tuple[str, str, str, str | None]]) -> None:
    if not ANNOTATION_ROOT.is_dir():
        return
    for path in sorted(ANNOTATION_ROOT.glob("*.json")):
        transcript_id = path.stem
        indexed = conn.execute("SELECT status FROM transcript WHERE transcript_id=?",
                               (transcript_id,)).fetchone()
        if not indexed or indexed[0] != "indexed":
            continue
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8-sig"))
            digest = sha256(raw)
            for beat in data.get("beat_map", []):
                start, end = beat.get("start_order"), beat.get("end_order")
                tags = beat.get("tags", [])
                values = [(str(tag), beat.get("narrative_function", ""), start, end)
                          for tag in tags]
                values.append((str(beat.get("narrative_function", "")),
                               beat.get("narrative_function", ""), start, end))
                insert_tags(conn, transcript_id, path, digest, values)
            for element in data.get("elements", []):
                orders = element.get("source_segment_orders", [])
                start, end = (min(orders), max(orders)) if orders else (None, None)
                values = [(str(element.get("category", "")), element.get("narrative_function", ""), start, end),
                          (str(element.get("narrative_function", "")), element.get("narrative_function", ""), start, end)]
                insert_tags(conn, transcript_id, path, digest, values)
        except (OSError, ValueError, TypeError) as exc:
            issues.append((str(path), "annotation_error", str(exc), None))


def insert_tags(conn: sqlite3.Connection, transcript_id: str, path: Path,
                digest: str, values: list[tuple]) -> None:
    for tag, function, start, end in values:
        tag_text = " ".join(part for part in (tag, str(function or "")) if part).strip()
        if not tag_text:
            continue
        start_id = caption_id(start) if isinstance(start, int) and start > 0 else None
        end_id = caption_id(end) if isinstance(end, int) and end > 0 else None
        conn.execute("INSERT OR IGNORE INTO narrative_tag VALUES(?,?,?,?,?,?,?,?,?)",
                     (transcript_id, tag_text, normalize_text(tag_text), tag or None,
                      start_id, end_id, str(path.relative_to(ROOT)), digest,
                      "legacy_unreviewed"))
