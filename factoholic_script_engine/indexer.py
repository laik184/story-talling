"""Incremental, read-only Markdown indexing for the configured corpus root."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .database import Database
from .parsing import ParsedTranscript, parse_markdown


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _decode(data: bytes) -> tuple[str, str | None]:
    try:
        return data.decode("utf-8-sig", errors="strict"), None
    except UnicodeDecodeError:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            try:
                return data.decode("utf-16", errors="strict"), None
            except UnicodeDecodeError:
                pass
        return data.decode("utf-8", errors="replace"), "decode_replacement_characters"


class DatasetIndexer:
    def __init__(self, config: EngineConfig, database: Database) -> None:
        self.config = config.resolved()
        self.database = database

    def _paths(self) -> tuple[Path, list[Path], list[dict[str, str]]]:
        root = self.config.dataset_dir
        if not root.exists():
            return root, [], [{"issue_type": "dataset_directory_missing", "relative_path": None, "details": str(root)}]
        if not root.is_dir():
            return root, [], [{"issue_type": "dataset_path_not_directory", "relative_path": None, "details": str(root)}]
        paths: list[Path] = []
        issues: list[dict[str, str]] = []
        for path in root.rglob("*"):
            try:
                if path.is_symlink():
                    issues.append({"issue_type": "symlink_skipped", "relative_path": path.relative_to(root).as_posix(), "details": "Symbolic links are not followed."})
                    continue
                if path.is_file() and path.suffix.casefold() == ".md":
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(root):
                        issues.append({"issue_type": "path_outside_dataset", "relative_path": path.relative_to(root).as_posix(), "details": "Resolved path leaves the configured dataset root."})
                        continue
                    paths.append(resolved)
            except (OSError, RuntimeError, ValueError) as error:
                try:
                    relative = path.relative_to(root).as_posix()
                except (ValueError, UnboundLocalError):
                    relative = None
                issues.append({"issue_type": "scan_error", "relative_path": relative, "details": f"{type(error).__name__}: {error}"})
        return root, sorted(paths, key=lambda item: item.as_posix().casefold()), issues

    def sync(self) -> dict[str, Any]:
        root, paths, issues = self._paths()
        now = utc_now()
        found: set[str] = set()
        added = updated = unchanged = unreadable = 0
        with self.database.connect() as connection:
            current = {
                row["relative_path"]: row
                for row in connection.execute("SELECT transcript_id, relative_path, content_hash, mtime_ns, file_size, first_seen_at FROM transcripts")
            }
            claimed_ids = {row["transcript_id"]: row["relative_path"] for row in current.values()}
            for path in paths:
                relative_path = path.relative_to(root).as_posix()
                found.add(relative_path)
                previous = current.get(relative_path)
                transcript_id = previous["transcript_id"] if previous else self._transcript_id(path.stem, relative_path, claimed_ids)
                claimed_ids[transcript_id] = relative_path
                try:
                    stat = path.stat()
                    data = path.read_bytes()
                    content_hash = hashlib.sha256(data).hexdigest()
                    if previous and previous["content_hash"] == content_hash:
                        connection.execute(
                            "UPDATE transcripts SET mtime_ns=?,file_size=?,file_status='available',last_indexed_at=? WHERE relative_path=?",
                            (stat.st_mtime_ns, stat.st_size, now, relative_path),
                        )
                        unchanged += 1
                        continue
                    raw_text, decode_warning = _decode(data)
                    parsed = parse_markdown(raw_text)
                    if decode_warning:
                        parsed.warnings.append(decode_warning)
                        parsed.parsing_status = "warning"
                    self._upsert(connection, transcript_id, path.name, relative_path, raw_text, parsed, content_hash, stat.st_mtime_ns, stat.st_size, now, previous["first_seen_at"] if previous else now)
                    connection.execute(
                        """INSERT INTO corpus_learning_state(transcript_id,content_hash,status,attempts,updated_at)
                        VALUES(?,?,'pending',0,?) ON CONFLICT(transcript_id) DO UPDATE SET
                        content_hash=excluded.content_hash,status='pending',attempts=0,last_batch_id=NULL,
                        last_error_json=NULL,updated_at=excluded.updated_at,analyzed_at=NULL,evidence_validated_at=NULL""",
                        (transcript_id, content_hash, now),
                    )
                    if previous is None:
                        added += 1
                    else:
                        updated += 1
                except (OSError, PermissionError) as error:
                    unreadable += 1
                    issues.append({"issue_type": "unreadable_file", "relative_path": relative_path, "details": f"{type(error).__name__}: {error}"})
                    self._mark_unreadable(connection, transcript_id, path.name, relative_path, now, current.get(relative_path))
                    connection.execute(
                        """INSERT INTO corpus_learning_state(transcript_id,content_hash,status,attempts,last_error_json,updated_at)
                        VALUES(?,?,'failed',0,?,?) ON CONFLICT(transcript_id) DO UPDATE SET
                        status='failed',last_error_json=excluded.last_error_json,updated_at=excluded.updated_at""",
                        (transcript_id, current[relative_path]["content_hash"] if relative_path in current else "", json.dumps({"reason": "transcript_unreadable"}), now),
                    )

            for relative_path in set(current) - found:
                connection.execute("UPDATE transcripts SET file_status='missing', last_indexed_at=? WHERE relative_path=?", (now, relative_path))
                connection.execute(
                    """UPDATE corpus_learning_state SET status='failed',last_error_json=?,updated_at=?
                    WHERE transcript_id=?""",
                    (json.dumps({"reason": "transcript_missing"}), now, current[relative_path]["transcript_id"]),
                )

            connection.execute("DELETE FROM index_issues")
            connection.executemany(
                "INSERT INTO index_issues(issue_type,relative_path,details,last_seen_at) VALUES(?,?,?,?)",
                [(item["issue_type"], item.get("relative_path"), item["details"], now) for item in issues],
            )
            connection.execute(
                "INSERT INTO instruction_versions(sha256,version,content,source_file,first_seen_at,last_seen_at) "
                "VALUES('index-scan','dataset-index-scan','',?,?,?) "
                "ON CONFLICT(sha256) DO UPDATE SET last_seen_at=excluded.last_seen_at, source_file=excluded.source_file",
                (str(root), now, now),
            )
        return {
            "dataset_dir": str(root),
            "discovered_markdown_files": len(paths),
            "added": added,
            "updated": updated,
            "unchanged": unchanged,
            "unreadable": unreadable,
            "scan_issues": issues,
            "synced_at": now,
        }

    @staticmethod
    def _transcript_id(stem: str, relative_path: str, claimed_ids: dict[str, str]) -> str:
        """Keep the filename stem when unique, with a stable path suffix on collisions."""
        if len(stem) <= 200 and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", stem) and stem not in claimed_ids:
            return stem
        digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "transcript"
        for length in (12, 16, 24, 32, 64):
            candidate = f"{base[:180]}_{digest[:length]}"
            if candidate not in claimed_ids or claimed_ids[candidate] == relative_path:
                return candidate
        raise RuntimeError("Could not derive a unique stable transcript ID from the relative file path.")

    @staticmethod
    def _upsert(
        connection: sqlite3.Connection,
        transcript_id: str,
        filename: str,
        relative_path: str,
        raw_markdown: str,
        parsed: ParsedTranscript,
        content_hash: str,
        mtime_ns: int,
        file_size: int,
        now: str,
        first_seen_at: str,
    ) -> None:
        metadata_text = "\n".join(f"{key}: {value}" for key, value in parsed.metadata.items())
        connection.execute(
            """INSERT INTO transcripts(
                transcript_id,filename,relative_path,title,creator,published,duration_seconds,views,likes,comments,rank,
                transcript_status,language_track,metadata_json,raw_markdown,transcript_text,analysis_text,content_hash,
                mtime_ns,file_size,file_status,parsing_status,warnings_json,caption_count,first_seen_at,last_indexed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(transcript_id) DO UPDATE SET
                filename=excluded.filename, relative_path=excluded.relative_path, title=excluded.title, creator=excluded.creator,
                published=excluded.published, duration_seconds=excluded.duration_seconds, views=excluded.views, likes=excluded.likes,
                comments=excluded.comments, rank=excluded.rank, transcript_status=excluded.transcript_status,
                language_track=excluded.language_track, metadata_json=excluded.metadata_json, raw_markdown=excluded.raw_markdown,
                transcript_text=excluded.transcript_text, analysis_text=excluded.analysis_text, content_hash=excluded.content_hash,
                mtime_ns=excluded.mtime_ns, file_size=excluded.file_size, file_status='available',
                parsing_status=excluded.parsing_status, warnings_json=excluded.warnings_json, caption_count=excluded.caption_count,
                last_indexed_at=excluded.last_indexed_at""",
            (
                transcript_id, filename, relative_path, parsed.title, parsed.creator, parsed.published, parsed.duration_seconds,
                parsed.views, parsed.likes, parsed.comments, parsed.rank, parsed.transcript_status, parsed.language_track,
                json.dumps(parsed.metadata, ensure_ascii=False), raw_markdown, parsed.transcript_text, parsed.analysis_text,
                content_hash, mtime_ns, file_size, "available", parsed.parsing_status,
                json.dumps(parsed.warnings, ensure_ascii=False), len(parsed.captions), first_seen_at, now,
            ),
        )
        connection.execute("DELETE FROM captions WHERE transcript_id=?", (transcript_id,))
        connection.execute("DELETE FROM caption_fts WHERE transcript_id=?", (transcript_id,))
        connection.executemany(
            "INSERT INTO captions(transcript_id,ordinal,timestamp,time_seconds,text,section,source_line) VALUES(?,?,?,?,?,?,?)",
            [(transcript_id, caption.ordinal, caption.timestamp, caption.time_seconds, caption.text, caption.section, caption.source_line) for caption in parsed.captions],
        )
        connection.executemany(
            "INSERT INTO caption_fts(transcript_id,ordinal,timestamp,section,text) VALUES(?,?,?,?,?)",
            [(transcript_id, str(caption.ordinal), caption.timestamp, caption.section or "", caption.text) for caption in parsed.captions if caption.text],
        )
        connection.execute("DELETE FROM transcript_fts WHERE transcript_id=?", (transcript_id,))
        connection.execute(
            "INSERT INTO transcript_fts(transcript_id,title,filename,metadata_text,transcript_text,analysis_text) VALUES(?,?,?,?,?,?)",
            (transcript_id, parsed.title or "", filename, metadata_text, parsed.transcript_text, parsed.analysis_text or ""),
        )

    @staticmethod
    def _mark_unreadable(connection: sqlite3.Connection, transcript_id: str, filename: str, relative_path: str, now: str, previous: sqlite3.Row | None) -> None:
        if previous:
            connection.execute("UPDATE transcripts SET file_status='unreadable', last_indexed_at=? WHERE relative_path=?", (now, relative_path))
            return
        connection.execute(
            """INSERT OR IGNORE INTO transcripts(
                transcript_id,filename,relative_path,metadata_json,raw_markdown,transcript_text,content_hash,
                file_status,parsing_status,warnings_json,first_seen_at,last_indexed_at
            ) VALUES(?,?,?,'{}','','','', 'unreadable','error','[\"unreadable_file\"]',?,?)""",
            (transcript_id, filename, relative_path, now, now),
        )
