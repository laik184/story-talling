"""Versioned record contract and deterministic identifiers; no semantic inference."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0.0"
CATEGORIES = {"hooks", "language", "humor", "story", "ending"}
STATES = {"A", "B", "C", "D"}
REVIEWS = {"source_only", "pending", "approved", "rejected", "needs_revision", "adaptation"}


class DnaValidationError(ValueError):
    """A deterministic contract or provenance requirement was not met."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_id(record: dict[str, Any]) -> str:
    if record.get("state") == "C":
        identity = f"C|{record.get('source_candidate_id','')}|{record.get('generic_template','')}"
    elif record.get("state") == "D":
        identity = f"D|{record.get('source_template_id','')}|{record.get('topic_id','')}|{record.get('adapted_line','')}"
    else:
        source = record.get("source", {})
        identity = "|".join((record.get("state", ""), source.get("transcript_id", ""),
            record.get("category", ""), ",".join(source.get("caption_ids", [])),
            record.get("writing_function", "")))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"WDNA_{digest}"


def validate_record(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise DnaValidationError("record must be a JSON object")
    required = {"dna_id", "state", "category", "source", "writing_function",
        "generic_template", "slots", "topic_fit", "language_register",
        "humor_emotional_function", "originality_risk", "review_status", "version",
        "created_at", "schema_version"}
    missing = sorted(required - record.keys())
    if missing:
        raise DnaValidationError(f"missing fields: {', '.join(missing)}")
    if record["state"] not in STATES:
        raise DnaValidationError("state must be A, B, C, or D")
    if record["category"] not in CATEGORIES:
        raise DnaValidationError("category must be hooks, language, humor, story, or ending")
    if record["schema_version"] != SCHEMA_VERSION:
        raise DnaValidationError(f"schema_version must be {SCHEMA_VERSION}")
    if not isinstance(record["version"], int) or record["version"] < 1:
        raise DnaValidationError("version must be a positive integer")
    if not isinstance(record["source"], dict):
        raise DnaValidationError("source must be an object")
    source = record["source"]
    for key in ("transcript_id", "relative_path", "sha256", "caption_ids", "timestamps", "excerpt"):
        if key not in source:
            raise DnaValidationError(f"source.{key} is required")
    if not source["caption_ids"] or len(source["caption_ids"]) != len(source["timestamps"]):
        raise DnaValidationError("source caption_ids and timestamps must be non-empty and aligned")
    if len(source["excerpt"]) > 220:
        raise DnaValidationError("source excerpt exceeds the 220-character evidence limit")
    if not re.fullmatch(r"[0-9a-f]{64}", str(source["sha256"])):
        raise DnaValidationError("source.sha256 must be a lowercase 64-character SHA-256")
    if not all(isinstance(x, str) and x for x in source["caption_ids"] + source["timestamps"]):
        raise DnaValidationError("caption IDs and timestamps must be non-empty strings")
    if not record["writing_function"] or not record["originality_risk"]:
        raise DnaValidationError("writing_function and originality_risk are required")
    if not isinstance(record["slots"], list) or not isinstance(record["topic_fit"], dict):
        raise DnaValidationError("slots must be a list and topic_fit must be an object")
    if record["state"] == "B" and record["review_status"] not in {"pending", "needs_revision", "rejected", "approved"}:
        raise DnaValidationError("B candidate has invalid review_status")
    if record["state"] == "C" and record["review_status"] != "approved":
        raise DnaValidationError("C approved template must have approved review_status")
    if record["state"] == "D" and (not record.get("source_template_id") or not record.get("fact_claim_ids")):
        raise DnaValidationError("D adaptation requires source_template_id and fact_claim_ids")
    if record["dna_id"] != make_id(record):
        raise DnaValidationError("dna_id does not match its deterministic identity")
    return record


def tokenize_query(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", value, flags=re.UNICODE)
