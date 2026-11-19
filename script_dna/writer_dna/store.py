"""SQLite/FTS5 persistence, version history and bounded lexical retrieval."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .model import (DnaValidationError, canonical_json, make_id, tokenize_query,
                    utc_now, validate_record)
from .source import SourceVerifier

class WriterDnaLibrary:
    def __init__(self, database_path: Path | None = None, project_root: Path | None = None):
        self.root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        self.path = database_path or self.root / "script_dna" / "writer_dna" / "library.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.verifier = SourceVerifier(self.root)
        self.initialize()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def session(self):
        db=self.connect()
        try:
            with db: yield db
        finally: db.close()

    def initialize(self) -> None:
        with self.session() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS dna_entry(
              dna_id TEXT PRIMARY KEY,state TEXT NOT NULL,category TEXT NOT NULL,
              source_id TEXT NOT NULL,review_status TEXT NOT NULL,version INTEGER NOT NULL,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS dna_entry_source ON dna_entry(source_id);
            CREATE INDEX IF NOT EXISTS dna_entry_filter ON dna_entry(state,category,review_status);
            CREATE TABLE IF NOT EXISTS dna_version(
              dna_id TEXT NOT NULL,version INTEGER NOT NULL,payload_json TEXT NOT NULL,
              saved_at TEXT NOT NULL,PRIMARY KEY(dna_id,version),
              FOREIGN KEY(dna_id) REFERENCES dna_entry(dna_id));
            CREATE TABLE IF NOT EXISTS review_event(
              event_id INTEGER PRIMARY KEY,dna_id TEXT NOT NULL,decision TEXT NOT NULL,
              reviewer TEXT NOT NULL,reviewer_type TEXT NOT NULL,reason TEXT NOT NULL,
              created_at TEXT NOT NULL,created_record_id TEXT,
              FOREIGN KEY(dna_id) REFERENCES dna_entry(dna_id));
            CREATE VIRTUAL TABLE IF NOT EXISTS dna_fts USING fts5(
              dna_id UNINDEXED,category,writing_function,generic_template,language_register,
              tokenize='unicode61');
            """)
            db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','1.0.0')")

    def _index(self, db: sqlite3.Connection, record: dict[str, Any]) -> None:
        db.execute("DELETE FROM dna_fts WHERE dna_id=?", (record["dna_id"],))
        db.execute("INSERT INTO dna_fts VALUES(?,?,?,?,?)", (record["dna_id"],record["category"],
            record["writing_function"],record.get("generic_template", ""),record["language_register"]))

    def add_record(self, record: dict[str, Any]) -> dict[str, Any]:
        record = validate_record(record)
        if record["state"] == "C":
            raise DnaValidationError("C approved templates can only be created by an explicit review decision")
        verified = self.verifier.validate(record["source"])
        if record["state"] == "B":
            if record.get("review_status") not in ("pending", "needs_revision", "rejected", "approved"):
                raise DnaValidationError("candidate status invalid")
            record = dict(record, review_status="pending")
        if record["state"] == "A" and record["review_status"] != "source_only":
            raise DnaValidationError("A source evidence must use source_only status")
        if record["state"] in {"B", "C"} and record.get("source_record_id"):
            with self.session() as db:
                source_row = db.execute("SELECT payload_json FROM dna_entry WHERE dna_id=?",
                    (record["source_record_id"],)).fetchone()
            if not source_row or json.loads(source_row[0])["state"] != "A":
                raise DnaValidationError("source_record_id must reference a stored A source record")
        if record["state"] == "D":
            template = self.get(record["source_template_id"])
            if not template or template["state"] != "C":
                raise DnaValidationError("D adaptation must reference an approved C template")
        payload = canonical_json(record)
        now = utc_now()
        with self.session() as db:
            old = db.execute("SELECT payload_json FROM dna_entry WHERE dna_id=?", (record["dna_id"],)).fetchone()
            if old:
                if old[0] != payload:
                    raise DnaValidationError("DNA ID already exists with different content; use a versioned update")
                return {"created": False, "dna_id": record["dna_id"], "duplicate": True, "verified": True}
            db.execute("INSERT INTO dna_entry VALUES(?,?,?,?,?,?,?,?,?)", (record["dna_id"],record["state"],
                record["category"],record["source"]["transcript_id"],record["review_status"],
                record["version"],record["created_at"],now,payload))
            db.execute("INSERT INTO dna_version VALUES(?,?,?,?)", (record["dna_id"],record["version"],payload,now))
            self._index(db, record)
        return {"created": True, "dna_id": record["dna_id"], "duplicate": False,
                "verified": True, "source_title": verified["title"]}

    def revise_candidate(self, dna_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with self.session() as db:
            row = db.execute("SELECT payload_json,review_status FROM dna_entry WHERE dna_id=? AND state='B'", (dna_id,)).fetchone()
            if not row:
                raise DnaValidationError("analyzed candidate not found")
            if row["review_status"] == "approved":
                raise DnaValidationError("approved candidate is immutable; create a new candidate")
            updated = json.loads(row["payload_json"])
            updated.update(changes)
            updated["version"] += 1
            updated["dna_id"] = dna_id
            updated["updated_at"] = utc_now()
            updated = validate_record(updated)
            self.verifier.validate(updated["source"])
            payload = canonical_json(updated)
            db.execute("UPDATE dna_entry SET review_status=?,version=?,updated_at=?,payload_json=? WHERE dna_id=?",
                (updated["review_status"],updated["version"],updated["updated_at"],payload,dna_id))
            db.execute("INSERT INTO dna_version VALUES(?,?,?,?)", (dna_id,updated["version"],payload,updated["updated_at"]))
            self._index(db, updated)
        return {"updated": True, "dna_id": dna_id, "version": updated["version"]}

    def review_candidate(self, dna_id: str, decision: str, reviewer: str, reason: str,
                         reviewer_type: str = "human") -> dict[str, Any]:
        decision = decision.upper()
        if decision not in {"APPROVE", "REJECT", "NEEDS_REVISION"}:
            raise DnaValidationError("decision must be APPROVE, REJECT, or NEEDS_REVISION")
        if reviewer_type != "human" or not reviewer.strip() or not reason.strip():
            raise DnaValidationError("state-changing review requires a named human reviewer and reason")
        with self.session() as db:
            row = db.execute("SELECT payload_json,review_status FROM dna_entry WHERE dna_id=? AND state='B'", (dna_id,)).fetchone()
            if not row or row["review_status"] not in {"pending", "needs_revision"}:
                raise DnaValidationError("candidate is missing or not reviewable")
            candidate = json.loads(row["payload_json"])
            approved_id = None
            if decision == "APPROVE":
                approved = dict(candidate, state="C", review_status="approved", version=1,
                    source_candidate_id=dna_id, reviewer=reviewer, reviewer_type=reviewer_type,
                    review_reason=reason, created_at=utc_now())
                approved["dna_id"] = make_id(approved)
                validate_record(approved)
                self.verifier.validate(approved["source"])
                payload = canonical_json(approved); now = utc_now()
                db.execute("INSERT INTO dna_entry VALUES(?,?,?,?,?,?,?,?,?)", (approved["dna_id"],"C",
                    approved["category"],approved["source"]["transcript_id"],"approved",1,
                    approved["created_at"],now,payload))
                db.execute("INSERT INTO dna_version VALUES(?,?,?,?)", (approved["dna_id"],1,payload,now))
                self._index(db, approved); approved_id = approved["dna_id"]
            new_status = {"APPROVE":"approved","REJECT":"rejected","NEEDS_REVISION":"needs_revision"}[decision]
            candidate.update(review_status=new_status,version=candidate["version"]+1,updated_at=utc_now())
            candidate_payload = canonical_json(candidate)
            db.execute("UPDATE dna_entry SET review_status=?,version=?,updated_at=?,payload_json=? WHERE dna_id=?",
                (new_status,candidate["version"],candidate["updated_at"],candidate_payload,dna_id))
            db.execute("INSERT INTO dna_version VALUES(?,?,?,?)", (dna_id,candidate["version"],candidate_payload,candidate["updated_at"]))
            db.execute("INSERT INTO review_event(dna_id,decision,reviewer,reviewer_type,reason,created_at,created_record_id) VALUES(?,?,?,?,?,?,?)",
                (dna_id,decision,reviewer,reviewer_type,reason,utc_now(),approved_id))
        return {"dna_id": dna_id, "decision": decision, "review_status": new_status,
                "approved_dna_id": approved_id}

    def get(self, dna_id: str) -> dict[str, Any] | None:
        with self.session() as db:
            row = db.execute("SELECT payload_json FROM dna_entry WHERE dna_id=?", (dna_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def search(self, query: str = "", category: str | None = None,
               review_status: str = "approved", transcript_id: str | None = None,
               dna_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        if not 1 <= limit <= 50:
            raise DnaValidationError("limit must be between 1 and 50")
        if review_status not in {"approved", "pending", "rejected", "needs_revision", "source_only", "all"}:
            raise DnaValidationError("invalid review_status filter")
        if category and category not in {"hooks","language","humor","story","ending"}:
            raise DnaValidationError("invalid category filter")
        tokens = tokenize_query(query)
        clauses=[]; params=[]
        if tokens:
            clauses.append("e.dna_id IN (SELECT dna_id FROM dna_fts WHERE dna_fts MATCH ?)")
            params.append(" AND ".join('"'+t.replace('"','""')+'"' for t in tokens))
        if category: clauses.append("e.category=?"); params.append(category)
        if review_status != "all": clauses.append("e.review_status=?"); params.append(review_status)
        if review_status == "approved": clauses.append("e.state='C'")
        if transcript_id: clauses.append("e.source_id=?"); params.append(transcript_id)
        if dna_id: clauses.append("e.dna_id=?"); params.append(dna_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.session() as db:
            rows=db.execute("SELECT e.payload_json FROM dna_entry e"+where+" ORDER BY e.category,e.dna_id LIMIT ?",(*params,limit)).fetchall()
            count=db.execute("SELECT count(*) FROM dna_entry e"+where,params).fetchone()[0]
        results=[json.loads(row[0]) for row in rows]
        return {"results":results,"count":len(results),"total_matches":count,
            "match_type":"exact_lexical_or_metadata","default_review_filter":review_status,
            "message":"No matching records." if not results else "Lexical retrieval only; semantic fit is not scored."}
    def rebuild_search_index(self) -> dict[str, int]:
        with self.session() as db:
            before=db.execute("SELECT count(*) FROM dna_entry").fetchone()[0]
            db.execute("DELETE FROM dna_fts")
            for row in db.execute("SELECT payload_json FROM dna_entry"):
                self._index(db,json.loads(row[0]))
            after=db.execute("SELECT count(*) FROM dna_entry").fetchone()[0]
        return {"entries_before":before,"entries_after":after,"fts_rows":after}
