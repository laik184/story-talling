"""Bounded lexical search and timestamped evidence retrieval over the local index."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from corpus_index_support import CORPUS_ROOT, DEFAULT_DB, normalize_text, word_tokens
from corpus_search_support import (
    MAX_LIMIT, MAX_QUERY, MAX_VARIANTS, caption_evidence, clip, contains_tokens,
    context_captions, keyword_expr, phrase_expr,
)


class CorpusSearch:
    def __init__(self, db_path: Path = DEFAULT_DB, corpus_root: Path = CORPUS_ROOT):
        self.db_path = db_path.resolve()
        self.corpus_root = corpus_root.resolve()
    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Corpus index is missing: {self.db_path}")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _source(self, row: sqlite3.Row) -> tuple[Path | None, str | None]:
        try:
            path = (self.corpus_root / row["relative_path"]).resolve(strict=True)
            path.relative_to(self.corpus_root)
            raw = path.read_bytes()
        except (OSError, ValueError) as exc:
            return None, f"unsafe, missing, or unreadable indexed path: {exc}"
        actual = hashlib.sha256(raw).hexdigest()
        if actual != row["source_sha256"]:
            return path, f"source hash changed: expected {row['source_sha256']}, found {actual}"
        return path, None

    def search_transcripts(self, query: str = "", query_variants: list[str] | None = None,
                           exact_phrase: bool = False, transcript_id: str | None = None,
                           narrative_functions: list[str] | None = None,
                           limit: int = 10, offset: int = 0) -> dict:
        if not isinstance(query, str) or len(query) > MAX_QUERY:
            raise ValueError(f"query must be text of at most {MAX_QUERY} characters.")
        if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}.")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer.")
        variants = [query] if query.strip() else []
        if query_variants is not None:
            if not isinstance(query_variants, list) or len(query_variants) > MAX_VARIANTS:
                raise ValueError(f"query_variants must contain at most {MAX_VARIANTS} strings.")
            if any(not isinstance(value, str) for value in query_variants):
                raise ValueError("Every query variant must be text.")
            variants.extend(query_variants)
        variants = list(dict.fromkeys(v.strip() for v in variants if isinstance(v, str) and v.strip()))
        if transcript_id is None and not variants:
            raise ValueError("Provide a query, query_variants, or transcript_id.")
        conn = self._connect()
        try:
            version = dict(conn.execute("SELECT key,value FROM meta")).get("schema_version")
            if version != "1":
                raise ValueError(f"Unsupported corpus index version: {version}")
            candidates: dict[str, dict] = {}
            warnings, stale = [], []
            if transcript_id:
                row = conn.execute("SELECT * FROM transcript WHERE transcript_id=?",
                                   (transcript_id,)).fetchone()
                if row and row["status"] == "indexed":
                    candidates[transcript_id] = {"row": row, "variant": None,
                        "match_type": "transcript_id", "bm25": None, "caption_ids": set()}
                elif not row:
                    warnings.append(f"Transcript ID not found: {transcript_id}")
                else:
                    warnings.append(f"Transcript ID is not searchable (status={row['status']}).")
            else:
                for variant in variants:
                    if len(variant) > MAX_QUERY:
                        warnings.append("An overlong query variant was skipped.")
                        continue
                    self._collect_variant(conn, variant, exact_phrase, candidates, warnings)
            for identity in list(candidates):
                item = candidates[identity]
                row = item["row"]
                _, problem = self._source(row)
                if problem:
                    stale.append({"transcript_id": identity, "reason": problem})
                    del candidates[identity]
            if narrative_functions:
                candidates = self._filter_functions(conn, candidates, narrative_functions)
                if conn.execute("SELECT COUNT(*) FROM narrative_tag").fetchone()[0] == 0:
                    warnings.append("No narrative-function annotations are indexed.")
                else:
                    warnings.append("Function tags come from legacy model annotations and are unreviewed.")
            scored = []
            for identity, item in candidates.items():
                row = item["row"]
                variant = item["variant"]
                tokens = word_tokens(variant or "")
                body, title = row["normalized_body"], row["normalized_title"]
                body_cov = (sum(contains_tokens(body, [t]) for t in tokens) / len(tokens)) if tokens else 1.0
                title_cov = (sum(contains_tokens(title, [t]) for t in tokens) / len(tokens)) if tokens else 1.0
                norm_q = normalize_text(variant or "")
                phrase_hit = bool(norm_q and (f" {norm_q} " in f" {body} " or
                                                f" {norm_q} " in f" {title} "))
                score = 1.0 if item["match_type"] == "transcript_id" else (
                    0.55 * body_cov + 0.20 * title_cov + 0.25 * float(phrase_hit))
                evidence = caption_evidence(conn, row, tokens, norm_q,
                                         exact_phrase and bool(variant), item["caption_ids"])
                if not evidence:
                    evidence = context_captions(conn, identity)
                    evidence = [{**cap, "match_type": "title_or_transcript_context"} for cap in evidence]
                scored.append({"row": row, "item": item, "score": round(score, 4),
                               "evidence": evidence})
            scored.sort(key=lambda x: (-x["score"], x["item"]["bm25"]
                                       if x["item"]["bm25"] is not None else 0.0,
                                       x["row"]["transcript_id"]))
            total = len(scored)
            results = [self._result(conn, entry) for entry in scored[offset:offset + limit]]
            return {"index_version": version, "query": query, "query_variants": variants[1:],
                    "exact_phrase": exact_phrase, "total_candidates": total,
                    "offset": offset, "limit": limit, "results": results,
                    "stale_sources": stale, "warnings": warnings,
                    "score_interpretation": "Lexical match score within this query; not semantic or storytelling quality."}
        finally:
            conn.close()
    def _collect_variant(self, conn: sqlite3.Connection, variant: str, exact: bool,
                         candidates: dict, warnings: list[str]) -> None:
        tokens = word_tokens(variant)
        try:
            expression = phrase_expr(variant) if exact else keyword_expr(variant)
        except ValueError:
            warnings.append(f"Query variant has no searchable tokens: {variant!r}")
            return
        hits: dict[str, dict] = {}
        for sql, kind in (
            ("SELECT transcript_id,bm25(transcript_fts,0,4,1) FROM transcript_fts WHERE transcript_fts MATCH ?", "transcript"),
            ("SELECT transcript_id,bm25(caption_fts,0,0,1) FROM caption_fts WHERE caption_fts MATCH ?", "caption")):
            try:
                for identity, rank in conn.execute(sql, (expression,)):
                    entry = hits.setdefault(identity, {"bm25": rank, "caption_ids": set(),
                                                       "kind": kind})
                    if rank < entry["bm25"]:
                        entry["bm25"] = rank
                    if kind == "caption":
                        # A second indexed lookup below retrieves matching cue IDs.
                        pass
            except sqlite3.OperationalError as exc:
                warnings.append(f"FTS query fell back to normalized text matching: {exc}")
        try:
            cap_sql = "SELECT transcript_id,caption_id FROM caption_fts WHERE caption_fts MATCH ?"
            for identity, caption_id in conn.execute(cap_sql, (expression,)):
                hits.setdefault(identity, {"bm25": 0.0, "caption_ids": set(),
                                           "kind": "caption"})["caption_ids"].add(caption_id)
        except sqlite3.OperationalError:
            pass
        norm = normalize_text(variant)
        for row in conn.execute("SELECT * FROM transcript WHERE status='indexed'"):
            full = f" {row['normalized_title']} {row['normalized_body']} "
            found = (f" {norm} " in full) if exact else contains_tokens(full, tokens)
            if found:
                hits.setdefault(row["transcript_id"], {"bm25": 0.0, "caption_ids": set(),
                                                        "kind": "normalized_fallback"})
        for identity, hit in hits.items():
            row = conn.execute("SELECT * FROM transcript WHERE transcript_id=? AND status='indexed'",
                               (identity,)).fetchone()
            if not row:
                continue
            kind = "exact_phrase" if exact and hit["kind"] != "normalized_fallback" else (
                "normalized_text_fallback" if hit["kind"] == "normalized_fallback" else hit["kind"])
            previous = candidates.get(identity)
            value = {"row": row, "variant": variant, "match_type": kind,
                     "bm25": hit["bm25"], "caption_ids": hit["caption_ids"]}
            if previous is None or len(tokens) > len(word_tokens(previous.get("variant") or "")):
                candidates[identity] = value
    @staticmethod
    def _filter_functions(conn: sqlite3.Connection, candidates: dict,
                          functions: list[str]) -> dict:
        terms = [normalize_text(value) for value in functions if isinstance(value, str) and value.strip()]
        if not terms:
            raise ValueError("narrative_functions must include nonempty strings.")
        allowed = set()
        for identity in candidates:
            tags = [row[0] for row in conn.execute(
                "SELECT normalized_tag FROM narrative_tag WHERE transcript_id=?", (identity,))]
            if any(term in tag for tag in tags for term in terms):
                allowed.add(identity)
        return {key: value for key, value in candidates.items() if key in allowed}
    def _result(self, conn: sqlite3.Connection, entry: dict) -> dict:
        row, item = entry["row"], entry["item"]
        duplicate_count = conn.execute(
            "SELECT COUNT(*) FROM transcript WHERE source_sha256=? AND status='indexed'",
            (row["source_sha256"],)).fetchone()[0]
        return {"transcript_id": row["transcript_id"], "title": row["title"],
                "relative_path": row["relative_path"], "source_sha256": row["source_sha256"],
                "duplicate_group": row["duplicate_group"] if duplicate_count > 1 else None,
                "match_type": item["match_type"], "matched_query_variant": item["variant"],
                "score": entry["score"], "bm25": item["bm25"],
                "score_interpretation": "Lexical retrieval relevance only; not storytelling quality.",
                "matched_captions": entry["evidence"]}
