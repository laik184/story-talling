"""Build a local, provenance-preserving search index for transcript Markdown."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from parser import parse_markdown
from corpus_annotation_index import index_existing_annotations
from corpus_index_support import (
    CORPUS_ROOT, DEFAULT_DB, FILENAME, SCHEMA_VERSION, caption_id, create_schema,
    normalize_text, remove_transcript, sha256,
)


class CorpusIndexer:
    def __init__(self, corpus_root: Path = CORPUS_ROOT, db_path: Path = DEFAULT_DB):
        self.corpus_root = corpus_root.resolve()
        self.db_path = db_path.resolve()
        if not self.corpus_root.is_dir():
            raise FileNotFoundError(f"Corpus directory missing: {self.corpus_root}")

    def _paths(self) -> tuple[list[tuple[Path, str]], list[tuple[str, str]]]:
        paths, issues = [], []
        for path in sorted(self.corpus_root.rglob("*.md")):
            try:
                resolved = path.resolve(strict=True)
                relative = resolved.relative_to(self.corpus_root).as_posix()
            except (OSError, ValueError) as exc:
                issues.append((str(path), f"unsafe or unreadable path: {exc}"))
                continue
            if resolved.is_file():
                paths.append((resolved, relative))
        return paths, issues

    def build(self, force: bool = False) -> dict:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=self.db_path.stem + ".", suffix=".tmp",
                                        dir=self.db_path.parent)
        os.close(fd)
        tmp = Path(tmp_name)
        counts = {"discovered": 0, "indexed": 0, "unchanged": 0, "updated": 0,
                  "parse_failed": 0, "invalid_name": 0, "missing": 0, "warnings": 0}
        issues: list[tuple[str, str, str | None]] = []
        conn = None
        try:
            if self.db_path.is_file() and not force:
                with closing(sqlite3.connect(self.db_path)) as old:
                    version = dict(old.execute("SELECT key,value FROM meta")).get("schema_version")
                    if version != SCHEMA_VERSION:
                        raise ValueError("Index schema mismatch; rebuild with force=True.")
                    with closing(sqlite3.connect(tmp)) as new:
                        old.backup(new)
            conn = sqlite3.connect(tmp)
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA foreign_keys=ON")
            create_schema(conn)
            conn.execute("DELETE FROM index_issue")
            paths, path_issues = self._paths()
            counts["discovered"] = len(paths)
            current_paths = {relative for _, relative in paths}
            for relative, detail in path_issues:
                issues.append((relative, "unsafe_path", detail, None))
            existing = {row[0]: row[1] for row in conn.execute(
                "SELECT relative_path, transcript_id FROM transcript")}
            for relative, transcript_id in existing.items():
                if relative not in current_paths:
                    conn.execute("UPDATE transcript SET status='missing' WHERE transcript_id=?",
                                 (transcript_id,))
                    conn.execute("INSERT OR REPLACE INTO index_issue VALUES(?,?,?,?)",
                                 (relative, "missing_source", "Source is absent from corpus.", None))
                    counts["missing"] += 1
            seen_ids: dict[str, str] = {}
            for path, relative in paths:
                match = FILENAME.match(path.name)
                if not match:
                    counts["invalid_name"] += 1
                    issues.append((relative, "invalid_filename", "Expected ####_<transcript_id>.md", None))
                    continue
                transcript_id = match.group(1)
                if transcript_id in seen_ids and seen_ids[transcript_id] != relative:
                    issues.append((relative, "duplicate_id", f"ID also used by {seen_ids[transcript_id]}", None))
                    counts["invalid_name"] += 1
                    continue
                seen_ids[transcript_id] = relative
                old_row = conn.execute(
                    "SELECT source_sha256,status FROM transcript WHERE transcript_id=?",
                    (transcript_id,)).fetchone()
                try:
                    raw = path.read_bytes()
                    digest = sha256(raw)
                except OSError as exc:
                    if old_row:
                        conn.execute("UPDATE transcript SET status='stale' WHERE transcript_id=?",
                                     (transcript_id,))
                        conn.execute("DELETE FROM transcript_fts WHERE transcript_id=?",
                                     (transcript_id,))
                        conn.execute("DELETE FROM caption_fts WHERE transcript_id=?",
                                     (transcript_id,))
                    issues.append((relative, "read_error", str(exc), None))
                    counts["parse_failed"] += 1
                    continue
                if old_row == (digest, "indexed"):
                    counts["unchanged"] += 1
                    counts["indexed"] += 1
                    continue
                remove_transcript(conn, transcript_id)
                try:
                    parsed = parse_markdown({"relative_path": relative}, raw)
                except (UnicodeError, ValueError, OSError) as exc:
                    title = raw.decode("utf-8", errors="replace").splitlines()[0][:500] if raw else ""
                    conn.execute("INSERT INTO transcript VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                        (transcript_id, relative, digest, title, None, "parse_failed",
                         normalize_text(title), "", json.dumps([str(exc)], ensure_ascii=True)))
                    issues.append((relative, "parse_error", str(exc), digest))
                    counts["parse_failed"] += 1
                    continue
                body = " ".join(row["text"] for row in parsed["segments"])
                warnings = parsed["warnings"]
                conn.execute("INSERT INTO transcript VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                    (transcript_id, relative, digest, parsed["title"],
                     parsed["duration_seconds"], "indexed", normalize_text(parsed["title"]),
                     normalize_text(body), json.dumps(warnings, ensure_ascii=True)))
                conn.execute("INSERT INTO transcript_fts(transcript_id,title,body) VALUES(?,?,?)",
                    (transcript_id, parsed["title"], body))
                for row in parsed["segments"]:
                    cue_id = caption_id(row["order"])
                    timestamp = row["timestamp"]
                    status = row["timestamp_status"]
                    text = row["text"]
                    conn.execute("INSERT INTO caption VALUES(?,?,?,?,?,?,?)",
                        (transcript_id, cue_id, row["order"], timestamp, status,
                         text, normalize_text(text)))
                    conn.execute("INSERT INTO caption_fts VALUES(?,?,?)",
                                 (transcript_id, cue_id, text))
                counts["indexed"] += 1
                if old_row:
                    counts["updated"] += 1
                counts["warnings"] += len(warnings)
            conn.execute("DELETE FROM narrative_tag")
            index_existing_annotations(conn, issues)
            for relative, kind, detail, digest in issues:
                conn.execute("INSERT OR REPLACE INTO index_issue VALUES(?,?,?,?)",
                             (relative, kind, detail or "", digest))
            groups = conn.execute(
                "SELECT source_sha256 FROM transcript WHERE status='indexed' "
                "GROUP BY source_sha256 HAVING COUNT(*)>1").fetchall()
            for (digest,) in groups:
                conn.execute("UPDATE transcript SET duplicate_group=? WHERE source_sha256=?",
                             (digest[:16], digest))
            conn.execute("INSERT OR REPLACE INTO meta VALUES('built_at_utc',?)",
                         (datetime.now(timezone.utc).isoformat(),))
            conn.execute("INSERT OR REPLACE INTO meta VALUES('corpus_root',?)",
                         (str(self.corpus_root),))
            conn.commit()
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise sqlite3.DatabaseError(f"SQLite integrity check failed: {check}")
            conn.close()
            conn = None
            os.replace(tmp, self.db_path)
            return {"index_path": str(self.db_path), "schema_version": SCHEMA_VERSION,
                    **counts, "issues": len(issues), "integrity_check": check}
        except Exception:
            if conn is not None:
                conn.close()
            tmp.unlink(missing_ok=True)
            Path(str(tmp) + "-journal").unlink(missing_ok=True)
            raise


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="Build a fresh index atomically.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--corpus", type=Path, default=CORPUS_ROOT)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(CorpusIndexer(args.corpus, args.db).build(force=args.rebuild),
                         ensure_ascii=True, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
