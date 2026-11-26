"""Application services shared by MCP tools, resources, and tests."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import EngineConfig
from .database import Database
from .indexer import DatasetIndexer, utc_now


class EngineError(Exception):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": False, "error": {"code": self.code, "message": self.message}}
        if self.details is not None:
            result["error"]["details"] = self.details
        return result


_TRANSCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_ALLOWED_STAGES = {
    "initialized", "instructions_retrieved", "reference_discovery", "reference_inspection",
    "reference_analysis", "outline_preparation", "outline_approval", "script_generation",
    "output_validation", "final_audit",
}
_CONTEXT_KEYS = {
    "research_context", "reference_mapping", "language_bank", "humor_opportunity_map",
    "storytelling_plan", "execution_prompt", "audit_notes",
}
_PATTERN_CATEGORIES = {
    "common_language", "hook", "hook_starter", "sentence_frame", "transition", "rehook",
    "reveal", "closing", "explanation", "humor", "dialogue", "reaction", "storytelling",
    "payoff", "relatable_example", "other",
}
_LEARNING_LEVELS = {"direct_reuse", "creative_adaptation", "first_principles", "original_creation"}
_SCRIPT_DNA_DIMENSIONS = (
    "Hook Type", "Curiosity Trigger", "Hook Duration", "Conversational Phrases", "Humor Type",
    "Humor Count and Timing", "Relatable Examples", "Character Dialogue", "Horizontal/Vertical Structure",
    "Storytelling Techniques", "Reveal/Payoff", "Ending Style", "Lexical Patterns and Vocabulary",
    "Formulaic Language", "Hook Words and Sentence Starters", "Sentence Frames and Construction",
    "Discourse Markers and Transitions", "Rehook, Reveal and Closing Language",
)
_LEARNING_BANK_CATEGORIES = {
    "common_vocabulary", "everyday_expression", "hook_starter", "sentence_frame", "funny_reaction",
    "explanation_mechanism", "discourse_transition", "humor_mechanism", "relatable_dialogue",
    "storytelling_technique", "script_dna",
}
_LEARNING_FACETS = {
    "topic": None,
    "language": {"common_vocabulary", "everyday_expression", "hook_starter", "funny_reaction", "discourse_transition"},
    "humor": {"humor_mechanism", "funny_reaction", "relatable_dialogue"},
    "construction": {"sentence_frame", "script_dna"},
    "explanation": {"explanation_mechanism"},
    "storytelling": {"storytelling_technique", "script_dna"},
    "vocabulary": {"common_vocabulary", "everyday_expression"},
    "hooks": {"hook_starter"},
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _fts_query(query: str, exact_phrase: bool) -> str:
    if exact_phrase:
        phrase = query.strip()
        if not phrase:
            raise EngineError("invalid_query", "The exact phrase must not be empty.")
        return '"' + phrase.replace('"', '""') + '"'
    tokens = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
    if not tokens:
        raise EngineError("invalid_query", "The query must contain at least one searchable word.")
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens[:24])


def _valid_iso_date(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise EngineError("invalid_date", f"{field} must use YYYY-MM-DD format.") from error


class ScriptEngine:
    def __init__(self, config: EngineConfig | None = None, auto_sync: bool = True) -> None:
        self.config = (config or EngineConfig.from_env()).resolved()
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.database = Database(self.config.database_file)
        self.indexer = DatasetIndexer(self.config, self.database)
        if auto_sync:
            self.indexer.sync()

    def refresh_index(self) -> dict[str, Any]:
        result = self.indexer.sync()
        result["ok"] = True
        return result

    def _create_corpus_learning_batch(
        self, connection: sqlite3.Connection, session_id: str, purpose: str, topic: str, instruction_sha256: str,
    ) -> dict[str, Any]:
        if purpose not in {"outline", "script"}:
            raise EngineError("invalid_learning_purpose", "purpose must be outline or script.")
        rows = connection.execute(
            "SELECT transcript_id,content_hash FROM transcripts WHERE file_status='available' ORDER BY relative_path"
        ).fetchall()
        if not rows:
            raise EngineError("empty_dataset", "No accessible transcripts are available for a full-corpus reading cycle.")
        batch_id = str(uuid.uuid4())
        now = utc_now()
        connection.execute(
            """INSERT INTO corpus_learning_batches(batch_id,session_id,purpose,topic,instruction_sha256,status,
            requested_items,item_count,created_at,updated_at) VALUES(?,?,?,?,?,'running',?,?,?,?)""",
            (batch_id, session_id, purpose, topic, instruction_sha256, len(rows), len(rows), now, now),
        )
        connection.executemany(
            """INSERT INTO corpus_learning_batch_items(batch_id,transcript_id,content_hash,sequence_no,status,updated_at)
            VALUES(?,?,?,?,'pending',?)""",
            [(batch_id, row["transcript_id"], row["content_hash"], index, now) for index, row in enumerate(rows, 1)],
        )
        for row in rows:
            previous = connection.execute(
                "SELECT content_hash FROM corpus_learning_state WHERE transcript_id=?", (row["transcript_id"],),
            ).fetchone()
            if previous is None or previous["content_hash"] != row["content_hash"]:
                connection.execute(
                    """INSERT INTO corpus_learning_state(transcript_id,content_hash,status,attempts,last_batch_id,updated_at)
                    VALUES(?,?,'pending',0,?,?) ON CONFLICT(transcript_id) DO UPDATE SET content_hash=excluded.content_hash,
                    status='pending',attempts=0,last_batch_id=excluded.last_batch_id,last_error_json=NULL,
                    updated_at=excluded.updated_at,analyzed_at=NULL,evidence_validated_at=NULL""",
                    (row["transcript_id"], row["content_hash"], batch_id, now),
                )
        connection.execute(
            "UPDATE session_context SET value_json=?,updated_at=? WHERE session_id=? AND context_key IN ('language_bank','humor_opportunity_map','storytelling_plan')",
            (_json({"invalidated_by_new_reading_cycle": batch_id}), now, session_id),
        )
        return {"batch_id": batch_id, "purpose": purpose, "topic": topic, "item_count": len(rows), "status": "running", "created_at": now}

    @staticmethod
    def _update_corpus_learning_batch_status(connection: sqlite3.Connection, batch_id: str) -> str:
        counts = connection.execute(
            "SELECT status,COUNT(*) AS n FROM corpus_learning_batch_items WHERE batch_id=? GROUP BY status", (batch_id,),
        ).fetchall()
        status_counts = {row["status"]: row["n"] for row in counts}
        total = sum(status_counts.values())
        terminal = status_counts.get("evidence_validated", 0) + status_counts.get("failed", 0) + status_counts.get("requires_review", 0)
        if total and terminal == total:
            status = "completed" if status_counts.get("evidence_validated", 0) == total else "requires_review"
            completed_at = utc_now()
        else:
            status, completed_at = "running", None
        connection.execute(
            "UPDATE corpus_learning_batches SET status=?,updated_at=?,completed_at=? WHERE batch_id=?",
            (status, utc_now(), completed_at, batch_id),
        )
        return status

    def _require_complete_corpus_cycle(
        self, connection: sqlite3.Connection, session_id: str, purpose: str, instructions_sha256: str,
        outline_approved_at: str | None = None,
    ) -> sqlite3.Row:
        batch = connection.execute(
            """SELECT * FROM corpus_learning_batches WHERE session_id=? AND purpose=? AND consumed_at IS NULL
            ORDER BY created_at DESC LIMIT 1""", (session_id, purpose),
        ).fetchone()
        if batch is None:
            raise EngineError("full_corpus_cycle_required", f"Start a fresh {purpose} corpus-reading cycle for this request.")
        counts = connection.execute(
            """SELECT COUNT(*) AS total,
            SUM(CASE WHEN i.status='evidence_validated' AND i.ai_inspected_at IS NOT NULL THEN 1 ELSE 0 END) AS validated,
            SUM(CASE WHEN i.delivered_at IS NOT NULL THEN 1 ELSE 0 END) AS delivered,
            SUM(CASE WHEN i.ai_inspected_at IS NOT NULL THEN 1 ELSE 0 END) AS ai_inspected,
            SUM(CASE WHEN i.analysis_submitted_at IS NOT NULL THEN 1 ELSE 0 END) AS analyzed
            FROM corpus_learning_batch_items i WHERE i.batch_id=?""", (batch["batch_id"],),
        ).fetchone()
        indexed_now = connection.execute("SELECT COUNT(*) FROM transcripts WHERE file_status='available'").fetchone()[0]
        current_hash_count = connection.execute(
            """SELECT COUNT(*) FROM corpus_learning_batch_items i JOIN transcripts t ON t.transcript_id=i.transcript_id
            WHERE i.batch_id=? AND t.file_status='available' AND t.content_hash=i.content_hash""", (batch["batch_id"],),
        ).fetchone()[0]
        failures = connection.execute(
            "SELECT transcript_id,status,error_json FROM corpus_learning_batch_items WHERE batch_id=? AND status IN ('failed','requires_review') ORDER BY sequence_no LIMIT 20",
            (batch["batch_id"],),
        ).fetchall()
        if batch["purpose"] != purpose or batch["instruction_sha256"] != instructions_sha256:
            raise EngineError("corpus_cycle_stale", "The reading cycle belongs to another operation or instruction version. Start a new cycle.")
        if outline_approved_at and batch["created_at"] < outline_approved_at:
            raise EngineError("fresh_script_cycle_required", "Script generation needs a new full-corpus reading cycle begun after outline approval.")
        if batch["status"] != "completed" or counts["total"] != batch["item_count"] or counts["validated"] != batch["item_count"]:
            raise EngineError("full_corpus_reading_incomplete", "The output gate is closed until every transcript has been delivered, AI-reported inspected, analyzed, and evidence-validated in this fresh cycle.", {
                "batch_id": batch["batch_id"], "batch_status": batch["status"], "total_corpus_files_at_start": batch["item_count"],
                "indexed_files_now": indexed_now, "cycle_files_still_current": current_hash_count,
                "delivered": counts["delivered"] or 0, "ai_reported_inspected": counts["ai_inspected"] or 0,
                "analyses_submitted": counts["analyzed"] or 0, "evidence_validated": counts["validated"] or 0,
                "pending": batch["item_count"] - (counts["validated"] or 0), "failed_or_review": [dict(row) for row in failures],
            })
        if indexed_now != batch["item_count"] or current_hash_count != batch["item_count"]:
            raise EngineError("corpus_cycle_stale", "The accessible corpus changed after this cycle started. Start a fresh full-corpus cycle.", {
                "cycle_file_count": batch["item_count"], "indexed_files_now": indexed_now, "cycle_files_still_current": current_hash_count,
            })
        for key in ("language_bank", "humor_opportunity_map", "storytelling_plan"):
            context = connection.execute("SELECT value_json FROM session_context WHERE session_id=? AND context_key=?", (session_id, key)).fetchone()
            value = _load_json(context["value_json"], {}) if context else {}
            if not isinstance(value, dict) or value.get("reading_cycle_id") != batch["batch_id"]:
                raise EngineError("topic_bank_missing", f"Build a fresh topic-specific {key} from this complete reading cycle before finalizing.", {"required_cycle_id": batch["batch_id"], "context_key": key})
            pattern_ids = value.get("pattern_ids", [])
            if not isinstance(pattern_ids, list):
                raise EngineError("invalid_topic_bank", f"{key}.pattern_ids must be a list.")
            if pattern_ids:
                found = connection.execute(
                    "SELECT COUNT(DISTINCT pattern_id) FROM corpus_learning_patterns WHERE batch_id=? AND session_id=? AND validation_status='evidence_validated' AND pattern_id IN (" + ",".join("?" for _ in pattern_ids) + ")",
                    (batch["batch_id"], session_id, *pattern_ids),
                ).fetchone()[0]
                if found != len(set(pattern_ids)):
                    raise EngineError("topic_bank_evidence_invalid", f"Some {key} entries are not backed by this cycle's evidence-validated learning bank.")
            elif not (isinstance(value.get("no_suitable_patterns_reason"), str) and value["no_suitable_patterns_reason"].strip()):
                raise EngineError("topic_bank_disposition_required", f"{key} needs evidence-validated pattern_ids or a reason explaining why no suitable pattern was found.")
        return batch

    def start_corpus_learning_batch(self, session_id: str, purpose: str | None = None) -> dict[str, Any]:
        self.indexer.sync()
        with self.database.connect() as connection:
            session = self._session(connection, session_id)
            purpose = purpose or session["operation"]
            if purpose == "outline" and session["operation"] not in {"outline", "script"}:
                raise EngineError("operation_not_requested", "An outline reading cycle requires an outline or script request.")
            if purpose == "script" and session["operation"] != "script":
                raise EngineError("operation_not_requested", "A script reading cycle requires an explicit script request.")
            instructions = self._load_instructions()
            if session["instruction_sha256"] != instructions["sha256"]:
                raise EngineError("instruction_version_changed", "Refresh session instructions before starting a corpus reading cycle.")
            batch = self._create_corpus_learning_batch(connection, session_id, purpose, session["topic"], session["instruction_sha256"])
            self._event(connection, session_id, "corpus_learning_batch_started", batch)
        return {"ok": True, **batch, "reading_rule": "Every accessible transcript is queued for this new cycle; prior analyses and banks do not count toward its coverage."}

    def get_corpus_learning_status(self, batch_id: str) -> dict[str, Any]:
        self.indexer.sync()
        with self.database.connect() as connection:
            batch = connection.execute("SELECT * FROM corpus_learning_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is None:
                raise EngineError("batch_not_found", f"No corpus reading cycle has ID {batch_id}.")
            counts = connection.execute(
                "SELECT status,COUNT(*) AS n FROM corpus_learning_batch_items WHERE batch_id=? GROUP BY status", (batch_id,),
            ).fetchall()
            current = connection.execute("SELECT COUNT(*) FROM transcripts WHERE file_status='available'").fetchone()[0]
            current_valid = connection.execute(
                """SELECT COUNT(*) FROM corpus_learning_batch_items i JOIN transcripts t ON t.transcript_id=i.transcript_id
                WHERE i.batch_id=? AND t.file_status='available' AND t.content_hash=i.content_hash""", (batch_id,),
            ).fetchone()[0]
            state_counts = connection.execute(
                "SELECT status,COUNT(*) AS n FROM corpus_learning_state GROUP BY status"
            ).fetchall()
        status_counts = {row["status"]: row["n"] for row in counts}
        return {
            "ok": True, "batch_id": batch_id, "session_id": batch["session_id"], "purpose": batch["purpose"],
            "topic": batch["topic"], "status": batch["status"], "total_corpus_files_at_start": batch["item_count"],
            "indexed_files_now": current, "cycle_files_still_current": current_valid,
            "coverage_stale": current != batch["item_count"] or current_valid != batch["item_count"],
            "counts": {key: status_counts.get(key, 0) for key in ("pending", "inspected", "analyzed", "evidence_validated", "failed", "requires_review")},
            "delivered_transcripts": sum(row["n"] for row in connection.execute("SELECT COUNT(*) AS n FROM corpus_learning_batch_items WHERE batch_id=? AND delivered_at IS NOT NULL", (batch_id,))),
            "ai_reported_inspected_transcripts": sum(row["n"] for row in connection.execute("SELECT COUNT(*) AS n FROM corpus_learning_batch_items WHERE batch_id=? AND ai_inspected_at IS NOT NULL", (batch_id,))),
            "submitted_semantic_analyses": sum(row["n"] for row in connection.execute("SELECT COUNT(*) AS n FROM corpus_learning_analyses WHERE batch_id=?", (batch_id,))),
            "evidence_validated_analyses": sum(row["n"] for row in connection.execute("SELECT COUNT(*) AS n FROM corpus_learning_analyses WHERE batch_id=? AND validation_status='evidence_validated'", (batch_id,))),
            "global_learning_state": {row["status"]: row["n"] for row in state_counts},
            "instructions_sha256": batch["instruction_sha256"], "created_at": batch["created_at"], "updated_at": batch["updated_at"],
        }

    def get_next_corpus_learning_batch(
        self, batch_id: str, max_transcripts: int = 2, max_chars: int = 50000,
    ) -> dict[str, Any]:
        self.indexer.sync()
        if not 1 <= max_transcripts <= 5 or not 5000 <= max_chars <= 150000:
            raise EngineError("invalid_batch_size", "Use 1–5 transcripts and 5000–150000 characters per response.")
        with self.database.connect() as connection:
            batch = connection.execute("SELECT * FROM corpus_learning_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is None:
                raise EngineError("batch_not_found", f"No corpus reading cycle has ID {batch_id}.")
            if batch["status"] in {"paused", "completed", "failed", "requires_review"}:
                raise EngineError("batch_not_running", f"This reading cycle is {batch['status']}; resume it or start a fresh cycle.")
            pending_validation = connection.execute(
                "SELECT transcript_id FROM corpus_learning_batch_items WHERE batch_id=? AND status='analyzed' ORDER BY sequence_no LIMIT 1", (batch_id,),
            ).fetchone()
            if pending_validation:
                return {"ok": True, "batch_id": batch_id, "next_action": "validate_analysis", "transcript_id": pending_validation["transcript_id"], "message": "A submitted analysis is awaiting evidence validation; validate it before reading more transcripts."}
            # Redeliver the oldest transcript if a client was interrupted after receiving it.
            rows = connection.execute(
                """SELECT i.*,t.filename,t.title,t.raw_markdown,t.analysis_text,t.content_hash AS current_hash,t.file_status
                FROM corpus_learning_batch_items i JOIN transcripts t ON t.transcript_id=i.transcript_id
                WHERE i.batch_id=? AND i.status='inspected' ORDER BY i.sequence_no LIMIT 1""", (batch_id,),
            ).fetchall()
            if not rows:
                rows = connection.execute(
                    """SELECT i.*,t.filename,t.title,t.raw_markdown,t.analysis_text,t.content_hash AS current_hash,t.file_status
                    FROM corpus_learning_batch_items i JOIN transcripts t ON t.transcript_id=i.transcript_id
                    WHERE i.batch_id=? AND i.status='pending' ORDER BY i.sequence_no LIMIT ?""", (batch_id, max_transcripts),
                ).fetchall()
            chosen: list[tuple[sqlite3.Row, dict[str, Any], int]] = []
            total_chars = 0
            for row in rows:
                if row["file_status"] != "available" or row["current_hash"] != row["content_hash"]:
                    connection.execute("UPDATE corpus_learning_batch_items SET status='requires_review',error_json=?,updated_at=? WHERE batch_id=? AND transcript_id=?", (_json({"reason": "transcript_changed_or_unavailable_after_cycle_started"}), utc_now(), batch_id, row["transcript_id"]))
                    continue
                captions = [dict(caption) for caption in connection.execute("SELECT timestamp,section,text,source_line FROM captions WHERE transcript_id=? ORDER BY ordinal", (row["transcript_id"],)).fetchall()]
                payload = {
                    "transcript_id": row["transcript_id"], "filename": row["filename"], "title": row["title"],
                    "content_hash": row["content_hash"], "original_markdown": row["raw_markdown"],
                    "existing_script_dna": row["analysis_text"],
                    "existing_dna_status": "must_be_reviewed_against_captions" if row["analysis_text"] else "not_present",
                    "captions": captions,
                }
                # Budget the complete serialized item, including repeated structured captions and DNA.
                size = len(_json(payload)) + 32
                if chosen and total_chars + size > max_chars:
                    break
                if not chosen and size > max_chars:
                    return {"ok": False, "error": {"code": "transcript_exceeds_client_batch_limit", "message": "Increase max_chars and retry; this transcript has not been delivered or counted.", "details": {"transcript_id": row["transcript_id"], "required_chars": size, "provided_max_chars": max_chars}}}
                payload["delivery_chars"] = size
                chosen.append((row, payload, size))
                total_chars += size
            if not chosen:
                status = self._update_corpus_learning_batch_status(connection, batch_id)
                return {"ok": True, "batch_id": batch_id, "next_action": "complete" if status == "completed" else "resolve_review_or_retry", "status": status, "items": [], "message": "No pending transcript could be delivered in this cycle."}
            now = utc_now()
            items = []
            for row, payload, size in chosen:
                connection.execute(
                    """UPDATE corpus_learning_batch_items SET status='inspected',attempts=attempts+1,
                    delivery_count=delivery_count+1,delivered_at=COALESCE(delivered_at,?),updated_at=?
                    WHERE batch_id=? AND transcript_id=?""",
                    (now, now, batch_id, row["transcript_id"]),
                )
                connection.execute(
                    """INSERT INTO session_references(session_id,transcript_id,discovered_at,last_query)
                    VALUES(?,?,?,?) ON CONFLICT(session_id,transcript_id) DO UPDATE SET discovered_at=COALESCE(session_references.discovered_at,excluded.discovered_at),last_query=excluded.last_query""",
                    (batch["session_id"], row["transcript_id"], now, f"full_corpus_cycle:{batch_id}"),
                )
                items.append(payload)
            connection.execute("UPDATE corpus_learning_batches SET updated_at=? WHERE batch_id=?", (now, batch_id))
        return {"ok": True, "batch_id": batch_id, "session_id": batch["session_id"], "purpose": batch["purpose"], "topic": batch["topic"], "next_action": "submit_analysis_for_each_transcript", "items_in_batch": len(items), "batch_chars": total_chars, "items": items, "delivery_is_not_comprehension": True}

    def pause_corpus_learning_batch(self, batch_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            batch = connection.execute("SELECT batch_id,status FROM corpus_learning_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is None:
                raise EngineError("batch_not_found", f"No corpus reading cycle has ID {batch_id}.")
            if batch["status"] == "running":
                connection.execute("UPDATE corpus_learning_batches SET status='paused',updated_at=? WHERE batch_id=?", (utc_now(), batch_id))
            return {"ok": True, "batch_id": batch_id, "status": "paused" if batch["status"] == "running" else batch["status"]}

    def resume_corpus_learning_batch(self, batch_id: str) -> dict[str, Any]:
        self.indexer.sync()
        with self.database.connect() as connection:
            batch = connection.execute("SELECT batch_id,status FROM corpus_learning_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is None:
                raise EngineError("batch_not_found", f"No corpus reading cycle has ID {batch_id}.")
            if batch["status"] != "paused":
                raise EngineError("batch_not_paused", f"Only a paused batch can be resumed; this one is {batch['status']}.")
            connection.execute("UPDATE corpus_learning_batches SET status='running',updated_at=?,completed_at=NULL WHERE batch_id=?", (utc_now(), batch_id))
        return {"ok": True, "batch_id": batch_id, "status": "running", "resume_rule": "Pending items continue; interrupted deliveries are redelivered without increasing unique coverage."}

    def submit_corpus_learning_analysis(
        self, batch_id: str, transcript_id: str, ai_reported_inspected: bool,
        summary: str, existing_dna_review: dict[str, Any], patterns: list[dict[str, Any]],
        topic_specific_analysis: dict[str, Any], no_suitable_patterns_reason: str | None = None,
    ) -> dict[str, Any]:
        self.indexer.sync()
        if ai_reported_inspected is not True:
            raise EngineError("inspection_attestation_required", "Set ai_reported_inspected=true only after reading the complete transcript contents returned by this cycle.")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
            raise EngineError("invalid_analysis", "summary must contain 1–4000 characters.")
        if not isinstance(existing_dna_review, dict) or len(_json(existing_dna_review)) > 100000:
            raise EngineError("invalid_dna_review", "existing_dna_review must be a JSON object under 100 KB.")
        if not isinstance(patterns, list) or len(patterns) > 100:
            raise EngineError("invalid_patterns", "patterns must be a list of at most 100 records.")
        required_facets = {"topic_relevance", "language", "hooks_and_frames", "humor", "explanation", "relatable_dialogue", "storytelling", "script_dna"}
        if (not isinstance(topic_specific_analysis, dict) or len(_json(topic_specific_analysis)) > 100000
                or required_facets - set(topic_specific_analysis)):
            raise EngineError("invalid_topic_analysis", "topic_specific_analysis must contain topic_relevance, language, hooks_and_frames, humor, explanation, relatable_dialogue, storytelling, and script_dna observations (use an explicit not-applicable reason where needed).")
        for facet in required_facets:
            value = topic_specific_analysis[facet]
            has_observations = isinstance(value, dict) and isinstance(value.get("observations"), list) and bool(value["observations"])
            has_reason = isinstance(value, dict) and isinstance(value.get("not_applicable_reason"), str) and bool(value["not_applicable_reason"].strip())
            if not (has_observations or has_reason):
                raise EngineError("invalid_topic_analysis", f"topic_specific_analysis.{facet} must contain observations or a not_applicable_reason.")
        if not patterns and (not isinstance(no_suitable_patterns_reason, str) or not no_suitable_patterns_reason.strip()):
            raise EngineError("pattern_disposition_required", "Submit evidence-backed patterns or explain why no suitable pattern was found in this fully read transcript.")
        now = utc_now()
        with self.database.connect() as connection:
            batch = connection.execute("SELECT * FROM corpus_learning_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is None:
                raise EngineError("batch_not_found", f"No corpus reading cycle has ID {batch_id}.")
            if batch["status"] != "running":
                raise EngineError("batch_not_running", f"Analysis submission requires a running cycle; this one is {batch['status']}.")
            if topic_specific_analysis.get("topic_relevance", {}).get("current_topic") != batch["topic"]:
                raise EngineError("topic_analysis_mismatch", "topic_specific_analysis.topic_relevance.current_topic must match the request topic in this cycle.")
            item = connection.execute(
                "SELECT * FROM corpus_learning_batch_items WHERE batch_id=? AND transcript_id=?", (batch_id, transcript_id),
            ).fetchone()
            if item is None or item["status"] not in {"inspected", "analyzed", "requires_review"}:
                raise EngineError("transcript_not_delivered", "This transcript has not been delivered in the current reading cycle.")
            source = connection.execute("SELECT * FROM transcripts WHERE transcript_id=?", (transcript_id,)).fetchone()
            if source is None or source["file_status"] != "available" or source["content_hash"] != item["content_hash"]:
                connection.execute(
                    "UPDATE corpus_learning_batch_items SET status='requires_review',error_json=?,updated_at=? WHERE batch_id=? AND transcript_id=?",
                    (_json({"reason": "source_changed_or_unavailable"}), now, batch_id, transcript_id),
                )
                self._update_corpus_learning_batch_status(connection, batch_id)
                return {"ok": False, "status": "requires_review", "error": {"code": "stale_transcript", "message": "The source changed during this cycle. Start a fresh full-corpus cycle."}}
            dna_status = existing_dna_review.get("status")
            dna_claims = existing_dna_review.get("claims")
            if source["analysis_text"]:
                if dna_status not in {"validated", "requires_review"} or existing_dna_review.get("complete_review") is not True or not isinstance(dna_claims, list):
                    raise EngineError("existing_dna_review_required", "Review the complete existing Script DNA, decompose its claims by dimension, and submit evidence for every claim marked supported.")
            elif dna_status != "not_present" or dna_claims != []:
                raise EngineError("unexpected_dna_review", "This source has no appended Script DNA; submit status='not_present' and an empty claims list.")
            for index, pattern in enumerate(patterns):
                if not isinstance(pattern, dict):
                    raise EngineError("invalid_pattern", f"patterns[{index}] must be an object.")
                category = pattern.get("bank_category")
                if category not in _LEARNING_BANK_CATEGORIES:
                    raise EngineError("invalid_pattern_category", f"patterns[{index}].bank_category must be one of {sorted(_LEARNING_BANK_CATEGORIES)}.")
                required_text = ("pattern", "reference_does", "why_works", "principle", "function", "original_equivalent_method")
                if any(not isinstance(pattern.get(key), str) or not pattern[key].strip() for key in required_text):
                    raise EngineError("invalid_pattern", f"patterns[{index}] is missing a required first-principles text field.")
                if len(pattern["pattern"]) > 160 or any(len(pattern[key]) > 4000 for key in required_text[1:]):
                    raise EngineError("pattern_too_long", f"patterns[{index}] exceeds the abstract pattern or analysis length limit.")
                if pattern.get("learning_level") not in _LEARNING_LEVELS:
                    raise EngineError("invalid_learning_level", f"patterns[{index}].learning_level must be one of {sorted(_LEARNING_LEVELS)}.")
                for list_key in ("suitable_applications", "avoid_applications", "evidence_quotes"):
                    value = pattern.get(list_key)
                    if not isinstance(value, list) or not value or len(value) > 20:
                        raise EngineError("invalid_pattern", f"patterns[{index}].{list_key} must be a nonempty list of at most 20 items.")
                if category == "script_dna" and pattern.get("script_dna_dimension") not in _SCRIPT_DNA_DIMENSIONS:
                    raise EngineError("invalid_script_dna_dimension", f"patterns[{index}] must name one of the 18 Script DNA dimensions.")
                if category != "script_dna" and pattern.get("script_dna_dimension") is not None:
                    raise EngineError("unexpected_script_dna_dimension", f"patterns[{index}] may only specify script_dna_dimension for the script_dna category.")
            analysis_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO corpus_learning_analyses(analysis_id,batch_id,session_id,transcript_id,content_hash,summary,
                existing_dna_status,existing_dna_review_json,topic_specific_analysis_json,ai_reported_inspected,semantic_status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (analysis_id, batch_id, batch["session_id"], transcript_id, item["content_hash"], summary, dna_status,
                 _json(existing_dna_review), _json({"analysis": topic_specific_analysis, "patterns": patterns, "no_suitable_patterns_reason": no_suitable_patterns_reason}),
                 1, "connected_ai_semantic_assessment_not_independently_verified", now),
            )
            connection.execute(
                """UPDATE corpus_learning_batch_items SET status='analyzed',ai_inspected_at=COALESCE(ai_inspected_at,?),
                analysis_submitted_at=?,updated_at=?,error_json=NULL WHERE batch_id=? AND transcript_id=?""",
                (now, now, now, batch_id, transcript_id),
            )
            connection.execute(
                """INSERT INTO corpus_learning_state(transcript_id,content_hash,status,attempts,last_batch_id,updated_at,analyzed_at)
                VALUES(?,?,'analyzed',1,?,?,?) ON CONFLICT(transcript_id) DO UPDATE SET content_hash=excluded.content_hash,
                status='analyzed',attempts=corpus_learning_state.attempts+1,last_batch_id=excluded.last_batch_id,
                last_error_json=NULL,updated_at=excluded.updated_at,analyzed_at=excluded.analyzed_at""",
                (transcript_id, item["content_hash"], batch_id, now, now),
            )
            connection.execute(
                "UPDATE session_references SET inspected_at=?,last_query=? WHERE session_id=? AND transcript_id=?",
                (now, f"full_corpus_cycle:{batch_id}", batch["session_id"], transcript_id),
            )
            connection.execute(
                "INSERT OR IGNORE INTO session_read_ranges(session_id,transcript_id,start_offset,end_offset) VALUES(?,?,0,?)",
                (batch["session_id"], transcript_id, len(source["raw_markdown"])),
            )
            self._update_corpus_learning_batch_status(connection, batch_id)
        return {"ok": True, "analysis_id": analysis_id, "batch_id": batch_id, "transcript_id": transcript_id, "status": "analyzed", "ai_reported_inspected": True, "evidence_validation": "pending", "semantic_status": "connected_ai_semantic_assessment_not_independently_verified", "next_step": "Call validate_corpus_learning_analysis before requesting another reading batch."}

    def validate_corpus_learning_analysis(self, batch_id: str, transcript_id: str) -> dict[str, Any]:
        self.indexer.sync()
        now = utc_now()
        failures: list[dict[str, Any]] = []
        with self.database.connect() as connection:
            batch = connection.execute("SELECT * FROM corpus_learning_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is None:
                raise EngineError("batch_not_found", f"No corpus reading cycle has ID {batch_id}.")
            item = connection.execute(
                "SELECT * FROM corpus_learning_batch_items WHERE batch_id=? AND transcript_id=?", (batch_id, transcript_id),
            ).fetchone()
            if item is None:
                raise EngineError("transcript_not_in_batch", f"Transcript {transcript_id} is not part of cycle {batch_id}.")
            if item["status"] == "evidence_validated":
                count = connection.execute("SELECT COUNT(*) FROM corpus_learning_patterns WHERE batch_id=? AND transcript_id=?", (batch_id, transcript_id)).fetchone()[0]
                return {"ok": True, "batch_id": batch_id, "transcript_id": transcript_id, "status": "evidence_validated", "validated_patterns": count, "idempotent": True}
            analysis = connection.execute(
                "SELECT * FROM corpus_learning_analyses WHERE batch_id=? AND transcript_id=? ORDER BY created_at DESC LIMIT 1", (batch_id, transcript_id),
            ).fetchone()
            if analysis is None or item["status"] != "analyzed":
                raise EngineError("analysis_not_submitted", "Submit a structured AI analysis after reading this transcript before evidence validation.")
            source = connection.execute("SELECT * FROM transcripts WHERE transcript_id=?", (transcript_id,)).fetchone()
            if source is None or source["file_status"] != "available" or source["content_hash"] != item["content_hash"]:
                failures.append({"error": "source_changed_or_unavailable"})
            dna_review = _load_json(analysis["existing_dna_review_json"], {})
            if source and source["analysis_text"]:
                if dna_review.get("status") != "validated" or dna_review.get("complete_review") is not True:
                    failures.append({"error": "existing_script_dna_not_fully_validated"})
                claims = dna_review.get("claims", [])
                if not claims:
                    failures.append({"error": "existing_script_dna_claims_not_decomposed"})
                for index, claim in enumerate(claims):
                    if not isinstance(claim, dict) or claim.get("verdict") != "supported":
                        failures.append({"claim_index": index, "error": "existing_dna_claim_requires_review"})
                        continue
                    if claim.get("dimension") not in _SCRIPT_DNA_DIMENSIONS or not isinstance(claim.get("claim"), str) or not claim["claim"].strip():
                        failures.append({"claim_index": index, "error": "invalid_existing_dna_claim"})
                        continue
                    quotes = claim.get("evidence_quotes")
                    if not isinstance(quotes, list) or not quotes:
                        failures.append({"claim_index": index, "error": "supported_claim_missing_caption_evidence"})
                        continue
                    for quote_index, evidence_quote in enumerate(quotes):
                        evidence = self._validate_one_evidence(connection, {"transcript_id": transcript_id, **evidence_quote}) if isinstance(evidence_quote, dict) else {"ok": False}
                        if not evidence.get("ok"):
                            failures.append({"claim_index": index, "quote_index": quote_index, "error": "existing_dna_evidence_not_found"})
            elif dna_review.get("status") != "not_present":
                failures.append({"error": "source_has_no_existing_dna_but_review_claims_it_does"})

            topic_data = _load_json(analysis["topic_specific_analysis_json"], {})
            patterns = topic_data.get("patterns", [])
            for index, pattern in enumerate(patterns):
                for quote_index, evidence_quote in enumerate(pattern.get("evidence_quotes", [])):
                    evidence = self._validate_one_evidence(connection, {"transcript_id": transcript_id, **evidence_quote}) if isinstance(evidence_quote, dict) else {"ok": False}
                    if not evidence.get("ok"):
                        failures.append({"pattern_index": index, "quote_index": quote_index, "error": "pattern_evidence_not_found"})
            if failures:
                error_json = _json({"failures": failures})
                connection.execute(
                    "UPDATE corpus_learning_batch_items SET status='requires_review',error_json=?,updated_at=? WHERE batch_id=? AND transcript_id=?",
                    (error_json, now, batch_id, transcript_id),
                )
                connection.execute("UPDATE corpus_learning_analyses SET validation_status='requires_review',validation_error_json=? WHERE analysis_id=?", (error_json, analysis["analysis_id"]))
                connection.execute(
                    """INSERT INTO corpus_learning_state(transcript_id,content_hash,status,attempts,last_batch_id,last_error_json,updated_at)
                    VALUES(?,?,'requires_review',1,?,?,?) ON CONFLICT(transcript_id) DO UPDATE SET status='requires_review',
                    last_batch_id=excluded.last_batch_id,last_error_json=excluded.last_error_json,updated_at=excluded.updated_at""",
                    (transcript_id, item["content_hash"], batch_id, error_json, now),
                )
                batch_status = self._update_corpus_learning_batch_status(connection, batch_id)
                return {"ok": False, "batch_id": batch_id, "transcript_id": transcript_id, "status": "requires_review", "batch_status": batch_status, "failures": failures, "note": "No learning-bank entries were accepted."}

            session = connection.execute("SELECT topic FROM sessions WHERE session_id=?", (batch["session_id"],)).fetchone()
            for pattern in patterns:
                pattern_id = str(uuid.uuid4())
                suitable_json = _json(pattern["suitable_applications"])
                avoid_json = _json(pattern["avoid_applications"])
                evidence_json = _json(pattern["evidence_quotes"])
                connection.execute(
                    """INSERT INTO corpus_learning_patterns(pattern_id,analysis_id,batch_id,session_id,transcript_id,content_hash,
                    bank_category,script_dna_dimension,pattern,reference_does,why_works,principle,function,suitable_applications_json,
                    avoid_applications_json,original_equivalent_method,evidence_quotes_json,learning_level,source_topic,target_topic,
                    validation_status,semantic_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pattern_id, analysis["analysis_id"], batch_id, batch["session_id"], transcript_id, item["content_hash"],
                     pattern["bank_category"], pattern.get("script_dna_dimension"), pattern["pattern"], pattern["reference_does"],
                     pattern["why_works"], pattern["principle"], pattern["function"], suitable_json, avoid_json,
                     pattern["original_equivalent_method"], evidence_json, pattern["learning_level"], source["title"], session["topic"],
                     "evidence_validated", "connected_ai_semantic_assessment_not_independently_verified", now),
                )
                connection.execute(
                    """INSERT INTO corpus_learning_fts(pattern_id,transcript_id,bank_category,script_dna_dimension,pattern,
                    reference_does,why_works,principle,function,suitable_applications,avoid_applications,original_equivalent_method,source_topic)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pattern_id, transcript_id, pattern["bank_category"], pattern.get("script_dna_dimension") or "", pattern["pattern"],
                     pattern["reference_does"], pattern["why_works"], pattern["principle"], pattern["function"], suitable_json,
                     avoid_json, pattern["original_equivalent_method"], source["title"] or ""),
                )
            connection.execute(
                "UPDATE corpus_learning_analyses SET validation_status='evidence_validated',validation_error_json=NULL WHERE analysis_id=?",
                (analysis["analysis_id"],),
            )
            connection.execute(
                """UPDATE corpus_learning_batch_items SET status='evidence_validated',evidence_validated_at=?,updated_at=?,error_json=NULL
                WHERE batch_id=? AND transcript_id=?""", (now, now, batch_id, transcript_id),
            )
            connection.execute(
                """INSERT INTO corpus_learning_state(transcript_id,content_hash,status,attempts,last_batch_id,last_error_json,updated_at,analyzed_at,evidence_validated_at)
                VALUES(?,?,'evidence_validated',1,?,NULL,?,?,?) ON CONFLICT(transcript_id) DO UPDATE SET
                content_hash=excluded.content_hash,status='evidence_validated',last_batch_id=excluded.last_batch_id,
                last_error_json=NULL,updated_at=excluded.updated_at,evidence_validated_at=excluded.evidence_validated_at""",
                (transcript_id, item["content_hash"], batch_id, now, analysis["created_at"], now),
            )
            batch_status = self._update_corpus_learning_batch_status(connection, batch_id)
            pattern_count = len(patterns)
        return {"ok": True, "batch_id": batch_id, "transcript_id": transcript_id, "status": "evidence_validated", "validated_patterns": pattern_count, "batch_status": batch_status, "semantic_status": "connected_ai_semantic_assessment_not_independently_verified", "note": "Every accepted quote matched a caption. The semantic interpretation remains an AI assessment."}

    def search_corpus_learning(
        self, query: str | None = None, facets: dict[str, str] | None = None,
        bank_categories: list[str] | None = None, session_id: str | None = None,
        limit: int = 30, offset: int = 0,
    ) -> dict[str, Any]:
        self.indexer.sync()
        if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise EngineError("invalid_pagination", "limit must be 1–100 and offset 0–10000.")
        if query is not None and (not isinstance(query, str) or not query.strip() or len(query) > 300):
            raise EngineError("invalid_query", "query must contain 1–300 characters when provided.")
        if facets is None:
            facets = {}
        if not isinstance(facets, dict) or len(facets) > 8:
            raise EngineError("invalid_facets", "facets must be an object with at most eight named searches.")
        unknown_facets = sorted(set(facets) - set(_LEARNING_FACETS))
        if unknown_facets:
            raise EngineError("invalid_facets", f"Unknown facet(s): {', '.join(unknown_facets)}.")
        for name, value in facets.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 300:
                raise EngineError("invalid_facets", f"facets.{name} must contain 1–300 characters.")
        if bank_categories is not None and (not isinstance(bank_categories, list) or set(bank_categories) - _LEARNING_BANK_CATEGORIES):
            raise EngineError("invalid_bank_categories", f"bank_categories must use values from {sorted(_LEARNING_BANK_CATEGORIES)}.")
        searches = dict(facets)
        if query:
            searches["all"] = query
        if not searches:
            raise EngineError("search_required", "Provide a query or one or more independent facets.")
        results: dict[str, dict[str, Any]] = {}
        with self.database.connect() as connection:
            if session_id:
                self._session(connection, session_id)
            for facet, text_query in searches.items():
                match = _fts_query(text_query, False)
                where = ["corpus_learning_fts MATCH ?", "p.validation_status='evidence_validated'", "t.file_status='available'", "t.content_hash=p.content_hash"]
                params: list[Any] = [match]
                category_set = _LEARNING_FACETS.get(facet)
                if category_set:
                    where.append("p.bank_category IN (" + ",".join("?" for _ in category_set) + ")")
                    params.extend(sorted(category_set))
                if bank_categories:
                    where.append("p.bank_category IN (" + ",".join("?" for _ in bank_categories) + ")")
                    params.extend(bank_categories)
                if session_id:
                    where.append("p.session_id=?")
                    params.append(session_id)
                rows = connection.execute(
                    f"""SELECT p.*,t.filename,t.title,b.topic AS target_topic,
                    bm25(corpus_learning_fts,0.0,0.0,0.0,0.0,2.0,1.5,1.2,1.4,1.2,1.0,0.9,1.1,0.4) AS score
                    FROM corpus_learning_fts JOIN corpus_learning_patterns p ON p.pattern_id=corpus_learning_fts.pattern_id
                    JOIN transcripts t ON t.transcript_id=p.transcript_id
                    JOIN corpus_learning_batches b ON b.batch_id=p.batch_id
                    WHERE {' AND '.join(where)} ORDER BY score ASC,p.created_at DESC LIMIT ? OFFSET ?""",
                    [*params, limit, offset],
                ).fetchall()
                for row in rows:
                    item = results.setdefault(row["pattern_id"], {**dict(row), "matched_facets": []})
                    item["matched_facets"].append(facet)
        ordered = sorted(results.values(), key=lambda item: (len(item["matched_facets"]), -float(item.get("score", 0))), reverse=True)[:limit]
        for item in ordered:
            for field in ("suitable_applications_json", "avoid_applications_json", "evidence_quotes_json"):
                item[field.removesuffix("_json")] = _load_json(item.pop(field), [])
        return {"ok": True, "returned": len(ordered), "query": query, "facet_queries": searches, "corpus_scope": "all current evidence-validated transcripts; results can be topically unrelated and are grouped by learning function", "patterns": ordered, "validation_status": "caption_evidence_matched", "semantic_status": "connected_ai_assessment_recorded_not_independently_verified"}

    def _load_instructions(self) -> dict[str, str]:
        path = self.config.instructions_file
        try:
            content = path.read_text(encoding="utf-8-sig")
        except OSError as error:
            raise EngineError("instructions_unavailable", f"Could not read Master Instructions at {path}: {error}") from error
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        version = digest[:12]
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO instruction_versions(sha256,version,content,source_file,first_seen_at,last_seen_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET last_seen_at=excluded.last_seen_at,source_file=excluded.source_file""",
                (digest, version, content, str(path), now, now),
            )
        return {"content": content, "version": version, "sha256": digest, "source_file": str(path)}

    def get_instructions(self, session_id: str | None = None) -> dict[str, Any]:
        current = self._load_instructions()
        if session_id:
            with self.database.connect() as connection:
                session = self._session(connection, session_id)
                connection.execute(
                    "UPDATE sessions SET instruction_sha256=?,instruction_version=?,stage='instructions_retrieved',updated_at=? WHERE session_id=?",
                    (current["sha256"], current["version"], utc_now(), session_id),
                )
                self._event(connection, session_id, "instructions_retrieved", {"sha256": current["sha256"], "previous_sha256": session["instruction_sha256"]})
        return {"ok": True, **current, "reloaded_from_disk": True}

    def get_workflow_contract(self) -> dict[str, Any]:
        path = self.config.workflow_file
        try:
            content = path.read_text(encoding="utf-8-sig")
        except OSError as error:
            raise EngineError("workflow_contract_unavailable", f"Could not read workflow contract at {path}: {error}") from error
        return {"ok": True, "content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "source_file": str(path)}

    def get_dataset_overview(self, refresh: bool = True) -> dict[str, Any]:
        sync = self.indexer.sync() if refresh else None
        with self.database.connect() as connection:
            totals = connection.execute(
                """SELECT
                    COUNT(*) AS indexed_records,
                    SUM(CASE WHEN file_status='available' THEN 1 ELSE 0 END) AS available_files,
                    SUM(CASE WHEN file_status='missing' THEN 1 ELSE 0 END) AS missing_files,
                    SUM(CASE WHEN file_status='unreadable' THEN 1 ELSE 0 END) AS unreadable_files,
                    SUM(CASE WHEN file_status='available' AND parsing_status IN ('error','warning') THEN 1 ELSE 0 END) AS malformed_files,
                    SUM(CASE WHEN parsing_status='warning' THEN 1 ELSE 0 END) AS parse_warnings,
                    SUM(CASE WHEN file_status='available' AND (caption_count=0 OR transcript_status IS NULL OR lower(transcript_status) NOT LIKE '%available%') THEN 1 ELSE 0 END) AS incomplete_files,
                    SUM(CASE WHEN creator IS NOT NULL AND trim(creator)<>'' THEN 1 ELSE 0 END) AS explicit_creator_metadata,
                    SUM(CASE WHEN file_status='available' AND analysis_text IS NOT NULL AND trim(analysis_text)<>'' THEN 1 ELSE 0 END) AS files_with_existing_analysis,
                    SUM(CASE WHEN file_status='available' THEN caption_count ELSE 0 END) AS total_captions,
                    SUM(CASE WHEN file_status='available' THEN file_size ELSE 0 END) AS total_bytes
                FROM transcripts"""
            ).fetchone()
            duplicate_rows = connection.execute(
                "SELECT content_hash,transcript_id,filename,relative_path FROM transcripts WHERE file_status='available' ORDER BY content_hash,relative_path"
            ).fetchall()
            rows = connection.execute("SELECT filename FROM transcripts WHERE file_status='available'").fetchall()
            problem_rows = connection.execute(
                "SELECT transcript_id,filename,relative_path,file_status,parsing_status,warnings_json,caption_count,transcript_status "
                "FROM transcripts WHERE file_status<>'available' OR parsing_status IN ('error','warning') "
                "OR caption_count=0 OR transcript_status IS NULL OR lower(transcript_status) NOT LIKE '%available%' "
                "ORDER BY relative_path"
            ).fetchall()
            issue_rows = connection.execute("SELECT issue_type,relative_path,details FROM index_issues ORDER BY issue_id").fetchall()
            creator_counts = connection.execute(
                "SELECT creator,COUNT(*) AS n FROM transcripts WHERE file_status='available' AND creator IS NOT NULL GROUP BY creator ORDER BY n DESC"
            ).fetchall()
            learning_counts = connection.execute(
                "SELECT status,COUNT(*) AS n FROM corpus_learning_state GROUP BY status"
            ).fetchall()
        available_names = [row["filename"] for row in rows]
        prefixes = sorted({int(match.group(1)) for name in available_names if (match := re.match(r"^(\d+)_", name))})
        numbering_gaps = []
        if prefixes:
            found = set(prefixes)
            numbering_gaps = [value for value in range(prefixes[0], prefixes[-1] + 1) if value not in found]
        duplicate_map: dict[str, list[dict[str, str]]] = {}
        for row in duplicate_rows:
            duplicate_map.setdefault(row["content_hash"], []).append({
                "transcript_id": row["transcript_id"], "filename": row["filename"], "relative_path": row["relative_path"],
            })
        duplicate_groups = [
            {"content_hash": digest, "files": files}
            for digest, files in duplicate_map.items() if len(files) > 1
        ]
        missing_files: list[dict[str, Any]] = []
        unreadable_files: list[dict[str, Any]] = []
        malformed_files: list[dict[str, Any]] = []
        incomplete_files: list[dict[str, Any]] = []
        parse_warning_files: list[dict[str, Any]] = []
        for row in problem_rows:
            item = {
                "transcript_id": row["transcript_id"], "filename": row["filename"],
                "relative_path": row["relative_path"], "file_status": row["file_status"],
                "parsing_status": row["parsing_status"], "warnings": _load_json(row["warnings_json"], []),
            }
            if row["file_status"] == "missing":
                missing_files.append(item)
            elif row["file_status"] == "unreadable":
                unreadable_files.append(item)
            if row["file_status"] == "available" and row["parsing_status"] in {"error", "warning"}:
                malformed_files.append(item)
            if row["file_status"] == "available" and row["parsing_status"] == "warning":
                parse_warning_files.append(item)
            status = (row["transcript_status"] or "").casefold()
            if row["file_status"] == "available" and (
                row["caption_count"] == 0 or not status or "available" not in status
            ):
                incomplete_files.append({**item, "caption_count": row["caption_count"], "transcript_status": row["transcript_status"]})
        return {
            "ok": True,
            "dataset_dir": str(self.config.dataset_dir),
            "storage_dir": str(self.config.state_dir),
            "counts": {
                "indexed_records": totals["indexed_records"] or 0,
                "available_files": totals["available_files"] or 0,
                "missing_files": totals["missing_files"] or 0,
                "unreadable_files": totals["unreadable_files"] or 0,
                "malformed_files": totals["malformed_files"] or 0,
                "files_with_parse_warnings": totals["parse_warnings"] or 0,
                "incomplete_or_unavailable_transcripts": totals["incomplete_files"] or 0,
                "duplicate_content_groups": len(duplicate_groups),
                "duplicate_extra_files": sum(len(group["files"]) - 1 for group in duplicate_groups),
                "files_with_existing_analysis": totals["files_with_existing_analysis"] or 0,
                "explicit_creator_metadata": totals["explicit_creator_metadata"] or 0,
                "total_captions": totals["total_captions"] or 0,
                "total_bytes": totals["total_bytes"] or 0,
            },
            "learning_state": {
                "note": "These persistent cross-cycle status counts are not proof of a fresh complete reading cycle for a new outline or script.",
                "by_status": {row["status"]: row["n"] for row in learning_counts},
            },
            "filename_numbering": {
                "numbered_files": len(prefixes),
                "minimum": prefixes[0] if prefixes else None,
                "maximum": prefixes[-1] if prefixes else None,
                "gaps": numbering_gaps,
                "note": "Numbering gaps are filename-prefix gaps; they do not by themselves prove missing source files.",
            },
            "creator_metadata": {
                "explicit_values": [{"creator": row["creator"], "file_count": row["n"]} for row in creator_counts],
                "operator_configured_dataset_creator": self.config.dataset_creator,
                "note": "Creator attribution is taken from explicit file metadata or the operator configuration; it is not inferred from writing style or titles.",
            },
            "file_details": {
                "missing_files": missing_files,
                "unreadable_files": unreadable_files,
                "malformed_files": malformed_files,
                "incomplete_or_unavailable_transcripts": incomplete_files,
                "files_with_parse_warnings": parse_warning_files,
                "duplicate_content_groups": duplicate_groups,
                "note": "All paths are relative to the configured dataset root. Categories may overlap (for example, a file can be both malformed and incomplete). Parse warnings are reported for review; warning does not always mean captions are unusable.",
            },
            "issues": [dict(row) for row in issue_rows],
            "last_sync": sync,
        }

    def _select_search(self, query: str, exact_phrase: bool, limit: int, offset: int, filters: list[tuple[str, Any]], table: str = "transcript_fts") -> list[sqlite3.Row]:
        if not 1 <= limit <= 50:
            raise EngineError("invalid_limit", "limit must be between 1 and 50.")
        if not 0 <= offset <= 100000:
            raise EngineError("invalid_offset", "offset must be between 0 and 100000.")
        match_query = _fts_query(query, exact_phrase)
        where = [f"{table} MATCH ?", "t.file_status='available'"]
        params: list[Any] = [match_query]
        for expression, value in filters:
            where.append(expression)
            params.append(value)
        if table == "transcript_fts":
            sql = f"""SELECT t.transcript_id,t.filename,t.relative_path,t.title,t.creator,t.published,t.duration_seconds,
                t.views,t.likes,t.comments,t.rank,t.transcript_status,t.language_track,t.caption_count,
                t.content_hash,t.analysis_text, bm25(transcript_fts,0.0,5.0,3.0,1.5,2.0,0.4) AS score,
                snippet(transcript_fts,4,'<match>','</match>',' … ',20) AS match_snippet
                FROM transcript_fts JOIN transcripts t ON t.transcript_id=transcript_fts.transcript_id
                WHERE {' AND '.join(where)} ORDER BY score ASC,t.transcript_id LIMIT ? OFFSET ?"""
        else:
            sql = f"""SELECT t.transcript_id,t.filename,t.relative_path,t.title,t.creator,t.published,t.duration_seconds,
                t.views,t.likes,t.comments,t.rank,t.transcript_status,t.language_track,t.caption_count,
                c.ordinal,c.timestamp,c.time_seconds,c.text,c.section,c.source_line,
                bm25(caption_fts,0.0,0.0,0.0,0.0,1.0) AS score
                FROM caption_fts JOIN transcripts t ON t.transcript_id=caption_fts.transcript_id
                JOIN captions c ON c.transcript_id=t.transcript_id AND c.ordinal=CAST(caption_fts.ordinal AS INTEGER)
                WHERE {' AND '.join(where)} ORDER BY score ASC,t.transcript_id,c.ordinal LIMIT ? OFFSET ?"""
        params.extend([limit, offset])
        try:
            with self.database.connect() as connection:
                return connection.execute(sql, params).fetchall()
        except sqlite3.OperationalError as error:
            # Keep the server useful on SQLite builds without FTS5; callers still receive an honest
            # literal search result, never a semantic-search claim.
            if "fts5" not in str(error).casefold() and "no such function: bm25" not in str(error).casefold():
                raise EngineError("search_failed", f"Search could not be completed: {error}") from error
            return self._fallback_search(query, exact_phrase, limit, offset, filters, table)

    def _fallback_search(self, query: str, exact_phrase: bool, limit: int, offset: int, filters: list[tuple[str, Any]], table: str) -> list[sqlite3.Row]:
        tokens = [query.casefold()] if exact_phrase else [token.casefold() for token in re.findall(r"[^\W_]+", query, flags=re.UNICODE)]
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM transcripts WHERE file_status='available'").fetchall()
            matched: list[sqlite3.Row] = []
            for row in rows:
                value = row["transcript_text"] if table == "caption_fts" else " ".join((row["title"] or "", row["filename"], row["metadata_json"], row["transcript_text"], row["analysis_text"] or ""))
                lowered = value.casefold()
                if not all(token in lowered for token in tokens):
                    continue
                matches = True
                for expression, expected in filters:
                    if "published" in expression:
                        if row["published"] is None or (row["published"] < expected if ">=" in expression else row["published"] > expected):
                            matches = False
                    elif "creator" in expression:
                        actual = row["creator"] or ""
                        if actual.casefold() != str(expected).casefold():
                            matches = False
                    elif "transcript_status" in expression:
                        if (row["transcript_status"] or "").casefold() != str(expected).casefold():
                            matches = False
                if matches:
                    matched.append(row)
            return matched[offset:offset + limit]

    @staticmethod
    def _result_item(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "transcript_id": row["transcript_id"],
            "filename": row["filename"],
            "title": row["title"],
            "creator": row["creator"],
            "published": row["published"],
            "duration_seconds": row["duration_seconds"],
            "rank": row["rank"],
            "transcript_status": row["transcript_status"],
            "caption_count": row["caption_count"],
            "snippet": row["match_snippet"] if "match_snippet" in row.keys() else row["text"],
            "score": row["score"] if "score" in row.keys() else None,
        }

    def search_references(
        self, query: str, creator: str | None = None, published_after: str | None = None,
        published_before: str | None = None, transcript_status: str | None = None,
        exact_phrase: bool = False, limit: int = 10, offset: int = 0, session_id: str | None = None,
    ) -> dict[str, Any]:
        self.indexer.sync()
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise EngineError("invalid_query", "query must contain 1–300 characters.")
        after = _valid_iso_date(published_after, "published_after")
        before = _valid_iso_date(published_before, "published_before")
        if after and before and after > before:
            raise EngineError("invalid_date_range", "published_after must not be later than published_before.")
        filters: list[tuple[str, Any]] = []
        if creator:
            filters.append(("lower(t.creator)=lower(?)", creator.strip()))
        if after:
            filters.append(("t.published>=?", after))
        if before:
            filters.append(("t.published<=?", before))
        if transcript_status:
            filters.append(("lower(t.transcript_status)=lower(?)", transcript_status.strip()))
        rows = self._select_search(query, exact_phrase, limit, offset, filters)
        results = [self._result_item(row) for row in rows]
        if session_id:
            with self.database.connect() as connection:
                self._session(connection, session_id)
                for result in results:
                    connection.execute(
                        """INSERT INTO session_references(session_id,transcript_id,discovered_at,last_query)
                        VALUES(?,?,?,?) ON CONFLICT(session_id,transcript_id) DO UPDATE SET discovered_at=excluded.discovered_at,last_query=excluded.last_query""",
                        (session_id, result["transcript_id"], utc_now(), query),
                    )
                self._set_stage(connection, session_id, "reference_discovery", {"query": query, "result_count": len(results)})
        return {"ok": True, "query": query, "search_type": "exact_phrase" if exact_phrase else "full_text_keyword", "corpus_scope": "all available indexed transcripts", "limit": limit, "offset": offset, "returned": len(results), "results": results, "semantic_interpretation": "not performed by local keyword retrieval"}

    def search_language_patterns(
        self, query: str, category_hint: str | None = None, exact_phrase: bool = False,
        limit: int = 20, offset: int = 0, session_id: str | None = None,
    ) -> dict[str, Any]:
        self.indexer.sync()
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise EngineError("invalid_query", "query must contain 1–300 characters.")
        rows = self._select_search(query, exact_phrase, limit, offset, [], table="caption_fts")
        items = [{
            "transcript_id": row["transcript_id"], "filename": row["filename"], "title": row["title"],
            "timestamp": row["timestamp"], "section": row["section"], "caption": row["text"],
            "source_line": row["source_line"], "creator": row["creator"],
            "evidence_status": "caption_text_retrieved; semantic_pattern_classification_pending",
        } for row in rows]
        if session_id:
            with self.database.connect() as connection:
                self._session(connection, session_id)
                for item in items:
                    connection.execute(
                        "INSERT INTO session_references(session_id,transcript_id,discovered_at,last_query) VALUES(?,?,?,?) "
                        "ON CONFLICT(session_id,transcript_id) DO UPDATE SET discovered_at=excluded.discovered_at,last_query=excluded.last_query",
                        (session_id, item["transcript_id"], utc_now(), query),
                    )
                self._set_stage(connection, session_id, "reference_discovery", {"query": query, "result_count": len(items), "kind": "language_pattern_search"})
        return {"ok": True, "query": query, "category_hint": category_hint, "search_type": "exact_phrase" if exact_phrase else "caption_keyword", "corpus_scope": "all available indexed captions", "returned": len(items), "results": items, "note": "The local index returns caption evidence; it does not decide whether a result is a hook, transition, humor device, or other technique."}

    def search_humor_patterns(
        self, query: str, creator: str = "FactoHolic", exact_phrase: bool = False,
        limit: int = 20, offset: int = 0, session_id: str | None = None,
    ) -> dict[str, Any]:
        self.indexer.sync()
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise EngineError("invalid_query", "query must contain 1–300 characters.")
        if not creator.strip():
            raise EngineError("invalid_creator", "A creator identity is required for humor-reference search.")
        if self.config.dataset_creator and self.config.dataset_creator.casefold() == creator.strip().casefold():
            filters = [("(t.creator IS NULL OR trim(t.creator)='' OR lower(t.creator)=lower(?))", creator.strip())]
        else:
            filters = [("lower(t.creator)=lower(?)", creator.strip())]
        rows = self._select_search(query, exact_phrase, limit, offset, filters, table="caption_fts")
        items = [{
            "transcript_id": row["transcript_id"], "filename": row["filename"], "title": row["title"],
            "timestamp": row["timestamp"], "section": row["section"], "caption": row["text"],
            "source_line": row["source_line"], "creator_metadata": row["creator"],
            "attribution_basis": "explicit_file_metadata" if row["creator"] else "operator_configured_dataset_creator",
            "evidence_status": "caption_text_retrieved; humor_quality_not_assessed",
        } for row in rows]
        with self.database.connect() as connection:
            metadata_creator_count = connection.execute("SELECT COUNT(*) FROM transcripts WHERE file_status='available' AND creator IS NOT NULL AND trim(creator)<>''").fetchone()[0]
        attribution_required = not self.config.dataset_creator and metadata_creator_count == 0
        if attribution_required:
            items = []
        if session_id:
            with self.database.connect() as connection:
                self._session(connection, session_id)
                for item in items:
                    connection.execute(
                        "INSERT INTO session_references(session_id,transcript_id,discovered_at,last_query) VALUES(?,?,?,?) "
                        "ON CONFLICT(session_id,transcript_id) DO UPDATE SET discovered_at=excluded.discovered_at,last_query=excluded.last_query",
                        (session_id, item["transcript_id"], utc_now(), query),
                    )
                self._set_stage(connection, session_id, "reference_discovery", {"query": query, "result_count": len(items), "kind": "humor_pattern_search"})
        return {
            "ok": True, "query": query, "requested_creator": creator, "corpus_scope": "FactoHolic-attributed captions only",
            "attribution_status": "operator_configured" if self.config.dataset_creator else ("explicit_metadata" if metadata_creator_count else "required"),
            "metadata_creator_count": metadata_creator_count,
            "operator_configured_dataset_creator": self.config.dataset_creator, "returned": len(items), "results": items,
            "limitation": "Keyword retrieval does not assess whether a line is humorous. If the collection lacks explicit creator metadata, configure FACTOHOLIC_DATASET_CREATOR only after confirming the dataset's provenance.",
        }

    def read_transcript(self, transcript_id: str, offset: int = 0, limit_chars: int = 100000, session_id: str | None = None) -> dict[str, Any]:
        self.indexer.sync()
        if not _TRANSCRIPT_ID_RE.fullmatch(transcript_id or ""):
            raise EngineError("invalid_transcript_id", "Use the indexed transcript ID or filename stem; filesystem paths are not accepted.")
        if not isinstance(offset, int) or offset < 0:
            raise EngineError("invalid_offset", "offset must be a non-negative character offset.")
        if not isinstance(limit_chars, int) or not 1 <= limit_chars <= 500000:
            raise EngineError("invalid_limit", "limit_chars must be between 1 and 500000.")
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM transcripts WHERE transcript_id=?", (transcript_id,)).fetchone()
            if row is None:
                raise EngineError("transcript_not_found", f"No indexed transcript has ID {transcript_id}.")
            if row["file_status"] != "available":
                raise EngineError("transcript_unavailable", f"The indexed file is currently {row['file_status']}.", {"filename": row["filename"]})
            raw = row["raw_markdown"]
            if offset > len(raw):
                raise EngineError("offset_out_of_range", f"offset exceeds transcript length ({len(raw)} characters).")
            end = min(len(raw), offset + limit_chars)
            page = raw[offset:end]
            next_offset = end if end < len(raw) else None
            inspected = False
            if session_id:
                self._session(connection, session_id)
                connection.execute(
                    "INSERT OR IGNORE INTO session_references(session_id,transcript_id,discovered_at) VALUES(?,?,?)",
                    (session_id, transcript_id, utc_now()),
                )
                connection.execute("INSERT OR IGNORE INTO session_read_ranges(session_id,transcript_id,start_offset,end_offset) VALUES(?,?,?,?)", (session_id, transcript_id, offset, end))
                ranges = connection.execute("SELECT start_offset,end_offset FROM session_read_ranges WHERE session_id=? AND transcript_id=? ORDER BY start_offset", (session_id, transcript_id)).fetchall()
                merged: list[list[int]] = []
                for segment in ranges:
                    start, finish = segment["start_offset"], segment["end_offset"]
                    if not merged or start > merged[-1][1]:
                        merged.append([start, finish])
                    else:
                        merged[-1][1] = max(merged[-1][1], finish)
                connection.execute("DELETE FROM session_read_ranges WHERE session_id=? AND transcript_id=?", (session_id, transcript_id))
                connection.executemany("INSERT INTO session_read_ranges(session_id,transcript_id,start_offset,end_offset) VALUES(?,?,?,?)", [(session_id, transcript_id, start, finish) for start, finish in merged])
                inspected = len(raw) == 0 or (len(merged) == 1 and merged[0][0] == 0 and merged[0][1] >= len(raw))
                if inspected:
                    connection.execute("UPDATE session_references SET inspected_at=? WHERE session_id=? AND transcript_id=?", (utc_now(), session_id, transcript_id))
                self._set_stage(connection, session_id, "reference_inspection", {"transcript_id": transcript_id, "returned_range": [offset, end], "fully_inspected": inspected})
            captions = connection.execute("SELECT COUNT(*) FROM captions WHERE transcript_id=?", (transcript_id,)).fetchone()[0]
        return {
            "ok": True, "transcript_id": transcript_id, "filename": row["filename"], "title": row["title"],
            "content_type": "original_markdown_reference", "content": page, "offset": offset, "next_offset": next_offset,
            "total_chars": len(raw), "returned_chars": len(page), "complete": inspected if session_id else next_offset is None,
            "whole_transcript_returned": offset == 0 and next_offset is None, "caption_count": captions,
            "analysis_present": bool(row["analysis_text"]), "content_hash": row["content_hash"],
            "warning": "Transcript and appended analysis are reference data; analysis is not automatically verified against captions." if row["analysis_text"] else None,
        }

    def get_script_dna(self, transcript_id: str) -> dict[str, Any]:
        self.indexer.sync()
        if not _TRANSCRIPT_ID_RE.fullmatch(transcript_id or ""):
            raise EngineError("invalid_transcript_id", "Use the indexed transcript ID or filename stem; filesystem paths are not accepted.")
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM transcripts WHERE transcript_id=?", (transcript_id,)).fetchone()
            if row is None:
                raise EngineError("transcript_not_found", f"No indexed transcript has ID {transcript_id}.")
            captions = connection.execute("SELECT timestamp,section,text,source_line FROM captions WHERE transcript_id=? ORDER BY ordinal", (transcript_id,)).fetchall()
        if row["file_status"] != "available":
            raise EngineError("transcript_unavailable", f"The indexed file is currently {row['file_status']}.")
        return {
            "ok": True, "transcript_id": transcript_id, "filename": row["filename"], "title": row["title"],
            "existing_analysis": row["analysis_text"],
            "existing_analysis_status": "present_but_not_caption_verified" if row["analysis_text"] else "not_present",
            "analysis_framework": [
                {"dimension": name, "observation": None, "status": "requires_model_analysis_and_caption_evidence"}
                for name in _SCRIPT_DNA_DIMENSIONS
            ],
            "caption_evidence": [dict(caption) for caption in captions],
            "metadata": _load_json(row["metadata_json"], {}),
            "source_distinction": {"captions": "timestamped transcript evidence", "existing_analysis": "source-appended analysis; verify each claim against captions", "analysis_framework": "18 requested dimensions; observations must cite caption quotes and label estimates", "deterministic_analysis": "timestamps and metadata only; no semantic Script DNA judgment performed"},
        }

    def _validate_one_evidence(self, connection: sqlite3.Connection, reference: dict[str, Any]) -> dict[str, Any]:
        transcript_id = reference.get("transcript_id")
        quote = reference.get("quote")
        if not isinstance(transcript_id, str) or not _TRANSCRIPT_ID_RE.fullmatch(transcript_id):
            return {"ok": False, "error": "invalid_transcript_id", "transcript_id": transcript_id}
        if not isinstance(quote, str) or not quote.strip():
            return {"ok": False, "error": "missing_quote", "transcript_id": transcript_id}
        row = connection.execute("SELECT filename,file_status FROM transcripts WHERE transcript_id=?", (transcript_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "source_not_found", "transcript_id": transcript_id}
        if row["file_status"] != "available":
            return {"ok": False, "error": "source_unavailable", "transcript_id": transcript_id, "filename": row["filename"]}
        timestamp = reference.get("timestamp")
        caption_rows = connection.execute("SELECT timestamp,time_seconds,source_line,text FROM captions WHERE transcript_id=? ORDER BY ordinal", (transcript_id,)).fetchall()
        norm_quote = _normalize_text(quote)
        matching = [caption for caption in caption_rows if norm_quote and norm_quote in _normalize_text(caption["text"])]
        if timestamp:
            matching = [caption for caption in matching if caption["timestamp"] == timestamp]
        if not matching:
            return {"ok": False, "error": "quote_or_timestamp_not_found", "transcript_id": transcript_id, "filename": row["filename"], "quote": quote, "timestamp": timestamp}
        return {
            "ok": True, "transcript_id": transcript_id, "filename": row["filename"], "quote": quote,
            "matched_locations": [{"timestamp": caption["timestamp"], "time_seconds": caption["time_seconds"], "source_line": caption["source_line"]} for caption in matching],
            "evidence_status": "exact_normalized_caption_match",
        }

    def validate_reference_evidence(self, references: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(references, list) or len(references) > 200:
            raise EngineError("invalid_references", "references must be a list of at most 200 source evidence objects.")
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        duplicates = []
        with self.database.connect() as connection:
            for reference in references:
                if not isinstance(reference, dict):
                    results.append({"ok": False, "error": "reference_must_be_object"})
                    continue
                key = (str(reference.get("transcript_id", "")), _normalize_text(str(reference.get("quote", ""))), str(reference.get("timestamp", "")))
                if key in seen:
                    duplicates.append(key[0])
                seen.add(key)
                results.append(self._validate_one_evidence(connection, reference))
        return {"ok": all(result.get("ok") for result in results) and not duplicates, "validated": len(results), "results": results, "duplicate_references": sorted(set(duplicates)), "validation_scope": "source existence, exact normalized caption text, optional exact timestamp; semantic interpretation is not verified"}

    def save_reference_analysis(
        self, transcript_id: str, analysis: dict[str, Any], patterns: list[dict[str, Any]], session_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(analysis, dict) or len(_json(analysis)) > 200000:
            raise EngineError("invalid_analysis", "analysis must be a JSON object under 200 KB.")
        if not isinstance(patterns, list) or len(patterns) > 100:
            raise EngineError("invalid_patterns", "patterns must be a list of at most 100 pattern records.")
        dna = analysis.get("script_dna", {})
        if not isinstance(dna, dict):
            raise EngineError("invalid_analysis", "analysis.script_dna must be an object keyed by the 18 Script DNA dimension names.")
        unknown_dimensions = sorted(set(dna) - set(_SCRIPT_DNA_DIMENSIONS))
        if unknown_dimensions:
            raise EngineError("invalid_analysis", f"Unknown Script DNA dimension(s): {', '.join(unknown_dimensions)}.")
        analysis_id = str(uuid.uuid4())
        now = utc_now()
        verified: list[tuple[dict[str, Any], dict[str, Any]]] = []
        failures: list[dict[str, Any]] = []
        with self.database.connect() as connection:
            source = connection.execute("SELECT file_status FROM transcripts WHERE transcript_id=?", (transcript_id,)).fetchone()
            if not source or source["file_status"] != "available":
                raise EngineError("source_unavailable", f"Transcript {transcript_id} is missing or unavailable.")
            if session_id:
                self._session(connection, session_id)
                inspected = connection.execute("SELECT inspected_at FROM session_references WHERE session_id=? AND transcript_id=?", (session_id, transcript_id)).fetchone()
                if not inspected or not inspected["inspected_at"]:
                    raise EngineError("source_not_fully_inspected", "Read the complete transcript in this session before saving reference analysis.")
            for index, pattern in enumerate(patterns):
                if not isinstance(pattern, dict):
                    failures.append({"index": index, "error": "pattern_must_be_object"})
                    continue
                category = str(pattern.get("category", "other"))
                if category not in _PATTERN_CATEGORIES:
                    failures.append({"index": index, "error": "invalid_category", "allowed": sorted(_PATTERN_CATEGORIES)})
                    continue
                level = str(pattern.get("learning_level", "creative_adaptation"))
                if level not in _LEARNING_LEVELS:
                    failures.append({"index": index, "error": "invalid_learning_level", "allowed": sorted(_LEARNING_LEVELS)})
                    continue
                if not all(isinstance(pattern.get(field), str) and pattern[field].strip() for field in ("pattern", "function", "evidence_quote")):
                    failures.append({"index": index, "error": "pattern_function_and_evidence_quote_required"})
                    continue
                if len(pattern["pattern"]) > 500 or len(pattern["function"]) > 2000 or len(pattern["evidence_quote"]) > 2000:
                    failures.append({"index": index, "error": "pattern_or_evidence_field_too_long"})
                    continue
                if pattern.get("explanation") is not None and (not isinstance(pattern["explanation"], str) or len(pattern["explanation"]) > 4000):
                    failures.append({"index": index, "error": "explanation_must_be_text_under_4000_characters"})
                    continue
                evidence = self._validate_one_evidence(connection, {"transcript_id": transcript_id, "quote": pattern["evidence_quote"], "timestamp": pattern.get("timestamp")})
                if evidence.get("ok"):
                    verified.append((pattern, evidence))
                else:
                    failures.append({"index": index, **evidence})
            verified_dna_quotes = 0
            for dimension, claim in dna.items():
                if not isinstance(claim, dict) or not isinstance(claim.get("observation"), str) or not claim["observation"].strip():
                    failures.append({"dimension": dimension, "error": "observation_required"})
                    continue
                if len(claim["observation"]) > 4000 or not isinstance(claim.get("evidence_quotes"), list) or not claim["evidence_quotes"]:
                    failures.append({"dimension": dimension, "error": "observation_requires_evidence_quotes_and_must_be_under_4000_characters"})
                    continue
                if len(claim["evidence_quotes"]) > 20:
                    failures.append({"dimension": dimension, "error": "at_most_20_evidence_quotes_per_dimension"})
                    continue
                if claim.get("estimate") is not None and not isinstance(claim["estimate"], bool):
                    failures.append({"dimension": dimension, "error": "estimate_must_be_boolean_when_provided"})
                    continue
                claim_failed = False
                for quote_index, evidence_quote in enumerate(claim["evidence_quotes"]):
                    if not isinstance(evidence_quote, dict):
                        failures.append({"dimension": dimension, "quote_index": quote_index, "error": "evidence_quote_must_be_object"})
                        claim_failed = True
                        continue
                    evidence = self._validate_one_evidence(connection, {
                        "transcript_id": transcript_id,
                        "quote": evidence_quote.get("quote"),
                        "timestamp": evidence_quote.get("timestamp"),
                    })
                    if not evidence.get("ok"):
                        failures.append({"dimension": dimension, "quote_index": quote_index, **evidence})
                        claim_failed = True
                    else:
                        verified_dna_quotes += 1
                if claim_failed:
                    continue
            if failures:
                return {"ok": False, "error": {"code": "analysis_evidence_invalid", "message": "Analysis was not stored because one or more pattern claims lack matching caption evidence."}, "verified_patterns": len(verified), "failures": failures}
            if not verified and not verified_dna_quotes:
                raise EngineError("analysis_evidence_required", "Submit at least one caption-backed pattern or Script DNA observation before saving analysis.")
            connection.execute(
                "INSERT INTO reference_analyses(analysis_id,transcript_id,session_id,analysis_json,evidence_status,semantic_status,created_at) VALUES(?,?,?,?,?,?,?)",
                (analysis_id, transcript_id, session_id, _json(analysis), "all_submitted_quotes_matched", "model_semantics_not_independently_verified", now),
            )
            pattern_ids = []
            for pattern, evidence in verified:
                pattern_id = str(uuid.uuid4())
                location = evidence["matched_locations"][0]
                connection.execute(
                    """INSERT INTO language_patterns(pattern_id,analysis_id,transcript_id,session_id,category,pattern,function,explanation,
                        evidence_quote,timestamp,source_line,learning_level,evidence_status,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pattern_id, analysis_id, transcript_id, session_id, pattern["category"], pattern["pattern"], pattern["function"], pattern.get("explanation"), pattern["evidence_quote"], location["timestamp"], location["source_line"], pattern.get("learning_level", "creative_adaptation"), "evidence_verified_semantics_unverified", now),
                )
                pattern_ids.append(pattern_id)
            if session_id:
                self._set_stage(connection, session_id, "reference_analysis", {"transcript_id": transcript_id, "analysis_id": analysis_id, "verified_patterns": len(pattern_ids)})
        return {"ok": True, "analysis_id": analysis_id, "transcript_id": transcript_id, "pattern_ids": pattern_ids, "evidence_status": "all_submitted_quotes_matched", "verified_script_dna_quotes": verified_dna_quotes, "semantic_status": "model_semantics_not_independently_verified", "saved_patterns": len(pattern_ids), "note": "Caption matches verify provenance only; the connected AI remains responsible for semantic analysis."}

    def get_language_bank(self, session_id: str | None = None, category: str | None = None, query: str | None = None, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise EngineError("invalid_limit", "limit must be between 1 and 500.")
        clauses = ["p.evidence_status='evidence_verified_semantics_unverified'", "t.file_status='available'"]
        params: list[Any] = []
        if session_id:
            clauses.append("p.session_id=?")
            params.append(session_id)
        if category:
            if category not in _PATTERN_CATEGORIES:
                raise EngineError("invalid_category", f"category must be one of: {', '.join(sorted(_PATTERN_CATEGORIES))}.")
            clauses.append("p.category=?")
            params.append(category)
        if query:
            clauses.append("(lower(p.pattern) LIKE lower(?) OR lower(p.function) LIKE lower(?) OR lower(coalesce(p.explanation,'')) LIKE lower(?))")
            needle = f"%{query}%"
            params.extend([needle, needle, needle])
        params.append(limit)
        with self.database.connect() as connection:
            if session_id:
                self._session(connection, session_id)
            rows = connection.execute(
                f"""SELECT p.*,t.filename,t.title FROM language_patterns p JOIN transcripts t ON t.transcript_id=p.transcript_id
                WHERE {' AND '.join(clauses)} ORDER BY p.created_at DESC LIMIT ?""", params,
            ).fetchall()
        return {"ok": True, "returned": len(rows), "patterns": [dict(row) for row in rows], "evidence_status": "caption quote and location were deterministically matched", "semantic_status": "pattern interpretation remains model-assessed"}

    def create_session(
        self, topic: str, operation: str, format: str | None = None, target_duration_seconds: int | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.indexer.sync()
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 1000:
            raise EngineError("invalid_topic", "topic must contain 1–1000 characters.")
        if operation not in {"ideas", "research", "outline", "script", "review", "analysis"}:
            raise EngineError("invalid_operation", "operation must be ideas, research, outline, script, review, or analysis.")
        if target_duration_seconds is not None and (not isinstance(target_duration_seconds, int) or not 1 <= target_duration_seconds <= 86400):
            raise EngineError("invalid_duration", "target_duration_seconds must be between 1 and 86400.")
        if constraints is not None and (not isinstance(constraints, dict) or len(_json(constraints)) > 50000):
            raise EngineError("invalid_constraints", "constraints must be a JSON object under 50 KB.")
        instructions = self._load_instructions()
        session_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO sessions(session_id,topic,operation,format,target_duration_seconds,constraints_json,instruction_sha256,
                instruction_version,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (session_id, topic.strip(), operation, format, target_duration_seconds, _json(constraints or {}), instructions["sha256"], instructions["version"], "instructions_retrieved", now, now),
            )
            self._event(connection, session_id, "session_initialized", {"topic": topic.strip(), "operation": operation, "format": format, "target_duration_seconds": target_duration_seconds})
            self._event(connection, session_id, "instructions_retrieved", {"sha256": instructions["sha256"], "version": instructions["version"]})
            learning_cycle = None
            if operation in {"outline", "script"}:
                learning_cycle = self._create_corpus_learning_batch(
                    connection, session_id, operation, topic.strip(), instructions["sha256"],
                )
                self._event(connection, session_id, "corpus_learning_batch_started", learning_cycle)
        return {
            "ok": True, "session_id": session_id, "stage": "instructions_retrieved", "topic": topic.strip(),
            "operation": operation, "format": format, "target_duration_seconds": target_duration_seconds,
            "constraints": constraints or {}, "master_instructions": instructions,
            "workflow_contract": self.get_workflow_contract(),
            "corpus_learning_cycle": learning_cycle,
            "next_step": "Process every transcript in corpus_learning_cycle with get_next_corpus_learning_batch, submit and validate each AI analysis, then build the three topic-specific plans. No outline or script was generated.",
        }

    @staticmethod
    def _session(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        if not isinstance(session_id, str) or not session_id:
            raise EngineError("invalid_session_id", "session_id is required.")
        row = connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise EngineError("session_not_found", f"No workflow session has ID {session_id}.")
        return row

    @staticmethod
    def _event(connection: sqlite3.Connection, session_id: str, event_type: str, details: dict[str, Any]) -> None:
        connection.execute("INSERT INTO session_events(session_id,event_type,details_json,created_at) VALUES(?,?,?,?)", (session_id, event_type, _json(details), utc_now()))

    def _set_stage(self, connection: sqlite3.Connection, session_id: str, stage: str, details: dict[str, Any]) -> None:
        if stage not in _ALLOWED_STAGES:
            raise EngineError("invalid_stage", f"Unsupported workflow stage {stage}.")
        connection.execute("UPDATE sessions SET stage=?,updated_at=? WHERE session_id=?", (stage, utc_now(), session_id))
        self._event(connection, session_id, stage, details)

    def get_session_context(self, session_id: str) -> dict[str, Any]:
        current = self._load_instructions()
        with self.database.connect() as connection:
            session = self._session(connection, session_id)
            contexts = connection.execute("SELECT context_key,value_json,updated_at FROM session_context WHERE session_id=? ORDER BY context_key", (session_id,)).fetchall()
            references = connection.execute(
                """SELECT sr.transcript_id,t.filename,t.title,sr.discovered_at,sr.inspected_at,sr.last_query
                FROM session_references sr JOIN transcripts t ON t.transcript_id=sr.transcript_id
                WHERE sr.session_id=? ORDER BY sr.discovered_at""", (session_id,),
            ).fetchall()
            outlines = connection.execute("SELECT outline_id,status,word_count,duration_estimate_seconds,fallback_reason,created_at,approved_at FROM outlines WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
            scripts = connection.execute("SELECT script_id,outline_id,word_count,estimated_duration_seconds,created_at FROM scripts WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
            events = connection.execute("SELECT event_type,details_json,created_at FROM session_events WHERE session_id=? ORDER BY event_id DESC LIMIT 50", (session_id,)).fetchall()
        return {
            "ok": True,
            "session": {
                "session_id": session["session_id"], "topic": session["topic"], "operation": session["operation"],
                "format": session["format"], "target_duration_seconds": session["target_duration_seconds"],
                "constraints": _load_json(session["constraints_json"], {}), "stage": session["stage"],
                "instruction_version": session["instruction_version"], "instruction_sha256": session["instruction_sha256"],
                "instructions_changed": session["instruction_sha256"] != current["sha256"],
                "current_instruction_version": current["version"], "created_at": session["created_at"], "updated_at": session["updated_at"],
            },
            "context": {row["context_key"]: {"value": _load_json(row["value_json"]), "updated_at": row["updated_at"]} for row in contexts},
            "references": [dict(row) for row in references], "outlines": [dict(row) for row in outlines],
            "scripts": [dict(row) for row in scripts],
            "recent_events": [{**dict(row), "details": _load_json(row["details_json"], {})} for row in events],
            "instructions_changed_action": "Call refresh_session_instructions before saving new outline/script content." if session["instruction_sha256"] != current["sha256"] else None,
        }

    def refresh_session_instructions(self, session_id: str) -> dict[str, Any]:
        instructions = self._load_instructions()
        with self.database.connect() as connection:
            session = self._session(connection, session_id)
            connection.execute("UPDATE sessions SET instruction_sha256=?,instruction_version=?,updated_at=? WHERE session_id=?", (instructions["sha256"], instructions["version"], utc_now(), session_id))
            self._event(connection, session_id, "instructions_retrieved", {"sha256": instructions["sha256"], "previous_sha256": session["instruction_sha256"], "explicit_refresh": True})
        return {"ok": True, "session_id": session_id, "master_instructions": instructions}

    def update_session_context(self, session_id: str, context_key: str, value: Any) -> dict[str, Any]:
        if context_key not in _CONTEXT_KEYS:
            raise EngineError("invalid_context_key", f"context_key must be one of: {', '.join(sorted(_CONTEXT_KEYS))}.")
        if len(_json(value)) > 200000:
            raise EngineError("context_too_large", "A context value may contain at most 200 KB of JSON.")
        now = utc_now()
        with self.database.connect() as connection:
            self._session(connection, session_id)
            if context_key in {"language_bank", "humor_opportunity_map", "storytelling_plan"}:
                if not isinstance(value, dict) or not isinstance(value.get("reading_cycle_id"), str):
                    raise EngineError("topic_bank_requires_cycle", f"{context_key} must be a structured object linked to reading_cycle_id.")
                cycle = connection.execute("SELECT * FROM corpus_learning_batches WHERE batch_id=? AND session_id=?", (value["reading_cycle_id"], session_id)).fetchone()
                if cycle is None or cycle["status"] != "completed":
                    raise EngineError("topic_bank_cycle_incomplete", f"{context_key} must use a completed reading cycle from this session.")
                if value.get("topic") != cycle["topic"]:
                    raise EngineError("topic_bank_topic_mismatch", f"{context_key}.topic must match the current request topic.")
                ids = value.get("pattern_ids", [])
                if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
                    raise EngineError("invalid_topic_bank", f"{context_key}.pattern_ids must be a list of pattern IDs.")
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    count = connection.execute(
                        f"SELECT COUNT(DISTINCT pattern_id) FROM corpus_learning_patterns WHERE batch_id=? AND session_id=? AND validation_status='evidence_validated' AND pattern_id IN ({placeholders})",
                        (cycle["batch_id"], session_id, *ids),
                    ).fetchone()[0]
                    if count != len(set(ids)):
                        raise EngineError("topic_bank_evidence_invalid", f"{context_key} includes IDs not validated in its reading cycle.")
                elif not (isinstance(value.get("no_suitable_patterns_reason"), str) and value["no_suitable_patterns_reason"].strip()):
                    raise EngineError("topic_bank_disposition_required", f"{context_key} needs validated pattern_ids or a documented no-suitable-patterns reason.")
            connection.execute(
                "INSERT INTO session_context(session_id,context_key,value_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(session_id,context_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (session_id, context_key, _json(value), now),
            )
            self._set_stage(connection, session_id, "reference_analysis", {"context_key": context_key})
        return {"ok": True, "session_id": session_id, "context_key": context_key, "updated_at": now, "stored": True}

    def _validate_session_references(self, connection: sqlite3.Connection, session_id: str, references: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not isinstance(references, list) or len(references) > 200:
            raise EngineError("invalid_references", "references must be a list of at most 200 source evidence objects.")
        results = [self._validate_one_evidence(connection, item) for item in references if isinstance(item, dict)]
        if len(results) != len(references):
            results.append({"ok": False, "error": "reference_must_be_object"})
        failures = [item for item in results if not item.get("ok")]
        reference_keys = [
            (str(item.get("transcript_id", "")), _normalize_text(str(item.get("quote", ""))), str(item.get("timestamp", "")))
            for item in references if isinstance(item, dict)
        ]
        if len(reference_keys) != len(set(reference_keys)):
            failures.append({"ok": False, "error": "duplicate_references"})
        if not failures:
            for item in results:
                inspected = connection.execute("SELECT inspected_at FROM session_references WHERE session_id=? AND transcript_id=?", (session_id, item["transcript_id"])).fetchone()
                if not inspected or not inspected["inspected_at"]:
                    failures.append({"ok": False, "error": "source_not_fully_inspected_in_session", "transcript_id": item["transcript_id"], "filename": item["filename"]})
        return results, failures

    def save_outline(
        self, session_id: str, content: str, references: list[dict[str, Any]],
        duration_estimate_seconds: int | None = None, fallback_reason: str | None = None,
    ) -> dict[str, Any]:
        self.indexer.sync()
        if not isinstance(content, str) or not content.strip() or len(content) > 500000:
            raise EngineError("invalid_outline", "content must contain 1–500000 characters.")
        if duration_estimate_seconds is not None and (
            not isinstance(duration_estimate_seconds, int) or not 1 <= duration_estimate_seconds <= 86400
        ):
            raise EngineError("invalid_duration", "duration_estimate_seconds must be between 1 and 86400.")
        outline_id = str(uuid.uuid4())
        words = len(content.split())
        now = utc_now()
        with self.database.connect() as connection:
            session = self._session(connection, session_id)
            if session["operation"] not in {"outline", "script"}:
                raise EngineError("operation_not_requested", f"This session is for {session['operation']}; outline saving requires an outline or script request.")
            instructions = self._load_instructions()
            if session["instruction_sha256"] != instructions["sha256"]:
                raise EngineError("instruction_version_changed", "Master Instructions changed during this session. Retrieve the current instructions with refresh_session_instructions before saving the outline.")
            corpus_cycle = self._require_complete_corpus_cycle(connection, session_id, "outline", instructions["sha256"])
            validated, failures = self._validate_session_references(connection, session_id, references)
            if not references and not (fallback_reason and fallback_reason.strip()):
                failures.append({"error": "references_or_fallback_reason_required"})
            if failures:
                return {"ok": False, "error": {"code": "outline_references_invalid", "message": "Outline was not saved; inspect and fully read each cited source or document an explicit fallback."}, "failures": failures}
            connection.execute("UPDATE corpus_learning_batches SET consumed_at=?,updated_at=? WHERE batch_id=?", (now, now, corpus_cycle["batch_id"]))
            connection.execute(
                "INSERT INTO outlines(outline_id,session_id,corpus_learning_batch_id,content,references_json,duration_estimate_seconds,word_count,fallback_reason,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (outline_id, session_id, corpus_cycle["batch_id"], content, _json(references), duration_estimate_seconds, words, fallback_reason, "pending_approval", now),
            )
            self._event(connection, session_id, "outline_preparation", {"outline_id": outline_id, "word_count": words})
            self._set_stage(connection, session_id, "outline_approval", {"outline_id": outline_id, "status": "pending_approval", "fallback_reason": fallback_reason})
        return {"ok": True, "outline_id": outline_id, "session_id": session_id, "status": "pending_approval", "word_count": words, "duration_estimate_seconds": duration_estimate_seconds, "references_validated": validated, "fallback_reason": fallback_reason, "note": "The outline remains unapproved until approve_outline is explicitly called."}

    def approve_outline(self, session_id: str, outline_id: str, approval_note: str) -> dict[str, Any]:
        if not isinstance(approval_note, str) or not approval_note.strip() or len(approval_note) > 1000:
            raise EngineError("explicit_approval_required", "Copy the user's explicit approval statement into approval_note (1–1000 characters).")
        now = utc_now()
        with self.database.connect() as connection:
            session = self._session(connection, session_id)
            if session["operation"] not in {"outline", "script"}:
                raise EngineError("operation_not_requested", f"This session is for {session['operation']}; outline approval requires an outline or script request.")
            outline = connection.execute("SELECT * FROM outlines WHERE outline_id=? AND session_id=?", (outline_id, session_id)).fetchone()
            if outline is None:
                raise EngineError("outline_not_found", f"Outline {outline_id} does not belong to session {session_id}.")
            if outline["status"] != "pending_approval":
                raise EngineError("outline_not_pending", f"Outline {outline_id} is not pending approval.")
            instructions = self._load_instructions()
            session = self._session(connection, session_id)
            if session["instruction_sha256"] != instructions["sha256"]:
                raise EngineError("instruction_version_changed", "Master Instructions changed. Refresh session instructions before approval.")
            connection.execute("UPDATE outlines SET status='approved',approved_at=? WHERE outline_id=?", (now, outline_id))
            self._set_stage(connection, session_id, "outline_approval", {"outline_id": outline_id, "status": "approved", "approval_note": approval_note})
        return {"ok": True, "session_id": session_id, "outline_id": outline_id, "status": "approved", "approved_at": now, "approval_note": approval_note}

    def get_approved_outline(self, session_id: str, outline_id: str | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._session(connection, session_id)
            if outline_id:
                rows = connection.execute("SELECT * FROM outlines WHERE session_id=? AND outline_id=? AND status='approved'", (session_id, outline_id)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM outlines WHERE session_id=? AND status='approved' ORDER BY approved_at DESC", (session_id,)).fetchall()
        return {"ok": True, "session_id": session_id, "returned": len(rows), "outlines": [{"outline_id": row["outline_id"], "content": row["content"], "references": _load_json(row["references_json"], []), "duration_estimate_seconds": row["duration_estimate_seconds"], "word_count": row["word_count"], "fallback_reason": row["fallback_reason"], "status": row["status"], "approved_at": row["approved_at"]} for row in rows]}

    def save_script(
        self, session_id: str, content: str, references: list[dict[str, Any]], outline_id: str | None = None,
        mappings: list[dict[str, Any]] | None = None, fallback_reason: str | None = None,
        speaking_rate_wpm: int = 130, pause_allowance_percent: float = 10.0,
    ) -> dict[str, Any]:
        self.indexer.sync()
        if not isinstance(content, str) or not content.strip() or len(content) > 1000000:
            raise EngineError("invalid_script", "content must contain 1–1000000 characters.")
        if not 60 <= speaking_rate_wpm <= 240:
            raise EngineError("invalid_speaking_rate", "speaking_rate_wpm must be between 60 and 240.")
        if not 0 <= pause_allowance_percent <= 50:
            raise EngineError("invalid_pause_allowance", "pause_allowance_percent must be between 0 and 50.")
        if mappings is None:
            mappings = []
        if not isinstance(mappings, list) or len(mappings) > 500:
            raise EngineError("invalid_mappings", "mappings must be a list of at most 500 records.")
        script_id = str(uuid.uuid4())
        words = len(content.split())
        estimated = words / speaking_rate_wpm * 60 * (1 + pause_allowance_percent / 100)
        now = utc_now()
        with self.database.connect() as connection:
            session = self._session(connection, session_id)
            if session["operation"] != "script":
                raise EngineError("operation_not_requested", f"This session is for {session['operation']}; script saving requires an explicit script request.")
            instructions = self._load_instructions()
            if session["instruction_sha256"] != instructions["sha256"]:
                raise EngineError("instruction_version_changed", "Master Instructions changed. Retrieve them with refresh_session_instructions before saving a script.")
            approved_at = None
            if outline_id:
                outline = connection.execute("SELECT status,approved_at FROM outlines WHERE outline_id=? AND session_id=?", (outline_id, session_id)).fetchone()
                if outline is None:
                    raise EngineError("outline_not_found", f"Outline {outline_id} does not belong to this session.")
                if outline["status"] != "approved":
                    raise EngineError("outline_not_approved", "A script cannot cite a pending or rejected outline. Explicitly approve the outline or omit outline_id for a direct-script request.")
                approved_at = outline["approved_at"]
            else:
                approved_outline = connection.execute("SELECT outline_id FROM outlines WHERE session_id=? AND status='approved' ORDER BY approved_at DESC LIMIT 1", (session_id,)).fetchone()
                if approved_outline:
                    raise EngineError("approved_outline_required", f"This session has approved outline {approved_outline['outline_id']}; pass that outline_id when saving the script.")
            corpus_cycle = self._require_complete_corpus_cycle(connection, session_id, "script", instructions["sha256"], approved_at)
            validated, failures = self._validate_session_references(connection, session_id, references)
            if not references and not (fallback_reason and fallback_reason.strip()):
                failures.append({"error": "references_or_fallback_reason_required"})
            mapping_results = []
            for index, mapping in enumerate(mappings):
                if not isinstance(mapping, dict):
                    failures.append({"index": index, "error": "mapping_must_be_object"})
                    continue
                excerpt = mapping.get("narration_excerpt")
                if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in content:
                    failures.append({"index": index, "error": "narration_excerpt_not_found_in_script"})
                    continue
                learned_pattern_id = mapping.get("learning_pattern_id")
                if learned_pattern_id:
                    learned = connection.execute(
                        """SELECT p.*,i.status AS cycle_item_status,t.content_hash AS current_hash FROM corpus_learning_patterns p
                        JOIN corpus_learning_batch_items i ON i.batch_id=p.batch_id AND i.transcript_id=p.transcript_id
                        JOIN transcripts t ON t.transcript_id=p.transcript_id
                        WHERE p.pattern_id=? AND p.session_id=? AND p.batch_id=? AND p.validation_status='evidence_validated'""",
                        (learned_pattern_id, session_id, corpus_cycle["batch_id"]),
                    ).fetchone()
                    if learned is None or learned["cycle_item_status"] != "evidence_validated" or learned["content_hash"] != learned["current_hash"]:
                        failures.append({"index": index, "error": "learning_pattern_not_validated_in_fresh_cycle", "learning_pattern_id": learned_pattern_id})
                        continue
                    if learned["learning_level"] == "direct_reuse" and _normalize_text(learned["pattern"]) not in _normalize_text(content):
                        failures.append({"index": index, "error": "claimed_direct_reuse_not_found", "learning_pattern_id": learned_pattern_id})
                        continue
                    mapping_results.append({"index": index, "learning_pattern_id": learned_pattern_id, "bank_category": learned["bank_category"], "learning_level": learned["learning_level"], "status": "source_evidence_and_narration_excerpt_present; semantic_fit_requires_model_review"})
                    continue
                pattern_id = mapping.get("pattern_id")
                if pattern_id:
                    pattern = connection.execute("SELECT * FROM language_patterns WHERE pattern_id=? AND evidence_status='evidence_verified_semantics_unverified'", (pattern_id,)).fetchone()
                    if pattern is None:
                        failures.append({"index": index, "error": "pattern_not_evidence_verified", "pattern_id": pattern_id})
                        continue
                    if pattern["session_id"] != session_id:
                        failures.append({"index": index, "error": "pattern_session_mismatch", "pattern_id": pattern_id})
                        continue
                    if pattern["transcript_id"] not in {ref.get("transcript_id") for ref in references if isinstance(ref, dict)}:
                        failures.append({"index": index, "error": "pattern_source_missing_from_script_references", "pattern_id": pattern_id})
                        continue
                    if pattern["learning_level"] == "direct_reuse" and _normalize_text(pattern["pattern"]) not in _normalize_text(content):
                        failures.append({"index": index, "error": "claimed_direct_reuse_not_found", "pattern_id": pattern_id})
                        continue
                    mapping_results.append({"index": index, "pattern_id": pattern_id, "status": "source_and_narration_present", "learning_level": pattern["learning_level"]})
                else:
                    mapping_results.append({"index": index, "status": "narration_excerpt_present; reference_pattern_not_linked"})
            if failures:
                return {"ok": False, "error": {"code": "script_validation_failed", "message": "Script was not saved because source, outline, or adaptation mapping checks failed."}, "failures": failures}
            if not any(item.get("learning_pattern_id") for item in mapping_results) and not (fallback_reason and fallback_reason.strip()):
                return {"ok": False, "error": {"code": "learning_application_required", "message": "Map at least one evidence-validated learned pattern to actual narration, or document why no suitable pattern could be applied."}, "failures": [{"error": "learning_pattern_mapping_or_fallback_required", "reading_cycle_id": corpus_cycle["batch_id"]}]}
            connection.execute("UPDATE corpus_learning_batches SET consumed_at=?,updated_at=? WHERE batch_id=?", (now, now, corpus_cycle["batch_id"]))
            connection.execute(
                """INSERT INTO scripts(script_id,session_id,corpus_learning_batch_id,outline_id,content,references_json,mappings_json,word_count,
                estimated_duration_seconds,speaking_rate_wpm,pause_allowance_percent,instruction_sha256,fallback_reason,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (script_id, session_id, corpus_cycle["batch_id"], outline_id, content, _json(references), _json(mappings), words, estimated, speaking_rate_wpm, pause_allowance_percent, instructions["sha256"], fallback_reason, now),
            )
            self._set_stage(connection, session_id, "script_generation", {"script_id": script_id, "outline_id": outline_id, "word_count": words, "fallback_reason": fallback_reason})
        return {"ok": True, "script_id": script_id, "session_id": session_id, "outline_id": outline_id, "word_count": words, "estimated_duration_seconds": round(estimated, 1), "target_duration_seconds": session["target_duration_seconds"], "speaking_rate_wpm": speaking_rate_wpm, "pause_allowance_percent": pause_allowance_percent, "references_validated": validated, "mapping_checks": mapping_results, "fallback_reason": fallback_reason, "semantic_review": "not performed by deterministic storage"}

    def validate_script(
        self, content: str | None = None, script_id: str | None = None, session_id: str | None = None,
        references: list[dict[str, Any]] | None = None, speaking_rate_wpm: int = 130,
        pause_allowance_percent: float = 10.0, fallback_reason: str | None = None,
    ) -> dict[str, Any]:
        if (content is None) == (script_id is None):
            raise EngineError("script_input_required", "Provide exactly one of content or script_id.")
        if content is not None and not isinstance(content, str):
            raise EngineError("invalid_script", "content must be text.")
        if not 60 <= speaking_rate_wpm <= 240 or not 0 <= pause_allowance_percent <= 50:
            raise EngineError("invalid_duration_assumption", "Speaking rate must be 60–240 WPM and pause allowance 0–50%.")
        if references is not None and (
            not isinstance(references, list) or len(references) > 200 or any(not isinstance(item, dict) for item in references)
        ):
            raise EngineError("invalid_references", "references must be a list of at most 200 evidence objects.")
        stored = None
        with self.database.connect() as connection:
            if script_id:
                stored = connection.execute("SELECT * FROM scripts WHERE script_id=?", (script_id,)).fetchone()
                if stored is None:
                    raise EngineError("script_not_found", f"No saved script has ID {script_id}.")
                content = stored["content"]
                references = _load_json(stored["references_json"], [])
                session_id = stored["session_id"]
                speaking_rate_wpm = stored["speaking_rate_wpm"]
                pause_allowance_percent = stored["pause_allowance_percent"]
                fallback_reason = stored["fallback_reason"]
            if content is None or not content.strip():
                raise EngineError("empty_script", "Script content must not be empty.")
            session = self._session(connection, session_id) if session_id else None
            ref_results = []
            ref_failures = []
            if references:
                ref_results, ref_failures = self._validate_session_references(connection, session_id, references) if session_id else ([self._validate_one_evidence(connection, item) for item in references], [])
                if not session_id:
                    ref_failures = [item for item in ref_results if not item.get("ok")]
            words = len(content.split())
            estimate = words / speaking_rate_wpm * 60 * (1 + pause_allowance_percent / 100)
            target = session["target_duration_seconds"] if session else None
            tolerance = max(5, target * 0.15) if target else None
            within_duration = abs(estimate - target) <= tolerance if target is not None else None
            checks = {
                "nonempty": True,
                "word_count": words,
                "estimated_duration_seconds": round(estimate, 1),
                "duration_assumption": {"speaking_rate_wpm": speaking_rate_wpm, "pause_allowance_percent": pause_allowance_percent},
                "target_duration_seconds": target,
                "estimated_duration_within_15_percent_or_5_seconds": within_duration,
                "references_provided": bool(references),
                "references_valid": not ref_failures if references else False,
                "source_or_fallback_documented": bool(references) or bool(fallback_reason and fallback_reason.strip()),
                "fallback_reason": fallback_reason,
                "reference_results": ref_results,
                "outline_approval": None,
                "instruction_version_current": None,
            }
            if stored and stored["outline_id"]:
                outline = connection.execute("SELECT status FROM outlines WHERE outline_id=?", (stored["outline_id"],)).fetchone()
                checks["outline_approval"] = outline["status"] == "approved" if outline else False
            if session:
                current = self._load_instructions()
                checks["instruction_version_current"] = session["instruction_sha256"] == current["sha256"]
                checks["saved_script_instruction_version_current"] = (
                    stored["instruction_sha256"] == current["sha256"] if stored else None
                )
                checks["session_operation_is_script"] = session["operation"] == "script"
                self._set_stage(connection, session_id, "output_validation", {"script_id": script_id, "word_count": words, "duration_estimate_seconds": round(estimate, 1)})
        passed = (
            not ref_failures
            and checks["source_or_fallback_documented"]
            and checks["instruction_version_current"] is not False
            and checks.get("saved_script_instruction_version_current") is not False
            and checks.get("session_operation_is_script") is not False
            and checks["outline_approval"] is not False
            and within_duration is not False
        )
        return {"ok": True, "passed_deterministic_checks": passed, "checks": checks, "not_deterministically_assessed": ["factual_reasoning", "natural_spoken_language", "humor_quality", "storytelling_effectiveness", "originality", "retention", "RLPA suitability"], "note": "Estimated duration is not a measured runtime; semantic checks require model review and sometimes human approval."}

    def get_audit_report(self, session_id: str) -> dict[str, Any]:
        current = self._load_instructions()
        with self.database.connect() as connection:
            session = self._session(connection, session_id)
            refs = connection.execute(
                """SELECT sr.transcript_id,t.filename,t.content_hash,t.caption_count,sr.discovered_at,sr.inspected_at
                FROM session_references sr JOIN transcripts t ON t.transcript_id=sr.transcript_id WHERE sr.session_id=? ORDER BY t.filename""", (session_id,),
            ).fetchall()
            outlines = connection.execute("SELECT outline_id,status,word_count,duration_estimate_seconds,fallback_reason,created_at,approved_at FROM outlines WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
            scripts = connection.execute("SELECT script_id,outline_id,word_count,estimated_duration_seconds,speaking_rate_wpm,pause_allowance_percent,instruction_sha256,fallback_reason,created_at FROM scripts WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
            analyses = connection.execute("SELECT analysis_id,transcript_id,evidence_status,semantic_status,created_at FROM reference_analyses WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
            learning_cycles = connection.execute("SELECT * FROM corpus_learning_batches WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
            corpus_total = connection.execute("SELECT COUNT(*) FROM transcripts WHERE file_status='available'").fetchone()[0]
            corpus_indexed = connection.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
            learning_coverage = []
            for cycle in learning_cycles:
                item_counts = {row["status"]: row["n"] for row in connection.execute(
                    "SELECT status,COUNT(*) AS n FROM corpus_learning_batch_items WHERE batch_id=? GROUP BY status", (cycle["batch_id"],)
                )}
                learning_coverage.append({
                    "batch_id": cycle["batch_id"], "purpose": cycle["purpose"], "topic": cycle["topic"],
                    "status": cycle["status"], "instruction_sha256": cycle["instruction_sha256"],
                    "total_corpus_files_at_start": cycle["item_count"], "indexed_files_now": corpus_indexed,
                    "accessible_files_now": corpus_total,
                    "delivered_transcripts": connection.execute("SELECT COUNT(*) FROM corpus_learning_batch_items WHERE batch_id=? AND delivered_at IS NOT NULL", (cycle["batch_id"],)).fetchone()[0],
                    "ai_reported_inspected_transcripts": connection.execute("SELECT COUNT(*) FROM corpus_learning_batch_items WHERE batch_id=? AND ai_inspected_at IS NOT NULL", (cycle["batch_id"],)).fetchone()[0],
                    "submitted_semantic_analyses": connection.execute("SELECT COUNT(*) FROM corpus_learning_analyses WHERE batch_id=?", (cycle["batch_id"],)).fetchone()[0],
                    "evidence_validated_analyses": connection.execute("SELECT COUNT(*) FROM corpus_learning_analyses WHERE batch_id=? AND validation_status='evidence_validated'", (cycle["batch_id"],)).fetchone()[0],
                    "pending_files": item_counts.get("pending", 0) + item_counts.get("inspected", 0) + item_counts.get("analyzed", 0),
                    "failed_files": item_counts.get("failed", 0), "requires_review_files": item_counts.get("requires_review", 0),
                    "unique_transcripts": connection.execute("SELECT COUNT(DISTINCT transcript_id) FROM corpus_learning_batch_items WHERE batch_id=? AND delivered_at IS NOT NULL", (cycle["batch_id"],)).fetchone()[0],
                    "consumed_at": cycle["consumed_at"],
                })
            self._set_stage(connection, session_id, "final_audit", {"reference_count": len(refs), "outline_count": len(outlines), "script_count": len(scripts)})
        return {
            "ok": True,
            "session": {"session_id": session_id, "topic": session["topic"], "operation": session["operation"], "format": session["format"], "target_duration_seconds": session["target_duration_seconds"], "stage": session["stage"]},
            "instruction_version": {"session_sha256": session["instruction_sha256"], "current_sha256": current["sha256"], "consistent": session["instruction_sha256"] == current["sha256"]},
            "reference_coverage": {"discovered_count": len(refs), "fully_inspected_count": sum(1 for ref in refs if ref["inspected_at"]), "actual_files": [{"transcript_id": ref["transcript_id"], "filename": ref["filename"], "content_hash": ref["content_hash"], "caption_count": ref["caption_count"], "discovered_at": ref["discovered_at"], "fully_inspected_at": ref["inspected_at"]} for ref in refs]},
            "reference_analyses": [dict(row) for row in analyses],
            "corpus_learning": {
                "indexed_files_now": corpus_indexed, "accessible_files_now": corpus_total,
                "cycles": learning_coverage,
                "completion_rule": "A cycle is complete only when every accessible transcript in its start snapshot was delivered, AI-reported inspected, analyzed, and evidence-validated; historical banks do not satisfy a new cycle.",
            },
            "outlines": [dict(row) for row in outlines], "scripts": [dict(row) for row in scripts],
            "deterministic_audit_only": True,
            "semantic_audit_required": ["claim accuracy", "language adaptation", "originality", "humor and sensitivity", "spoken flow", "storytelling and retention", "hook payoff"],
            "limitations": ["Search and indexing do not mean every transcript has been read.", "A matched quote verifies source presence, not the connected AI's interpretation.", "Estimated duration is derived from the stated speaking-rate and pause assumptions."],
        }
