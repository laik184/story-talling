"""Minimal local stdio MCP façade over the deterministic Step 2.2 interface."""
from __future__ import annotations

import sqlite3
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from corpus_search import CorpusSearch
from corpus_evidence import CorpusEvidence
from client_interface import FactoHolicClientInterface
from writer_dna.store import WriterDnaLibrary

service = FactoHolicClientInterface()
corpus_search = CorpusSearch()
corpus_evidence = CorpusEvidence()
writer_dna_library = WriterDnaLibrary()
mcp = FastMCP(
    "FactoHolic Dynamic Script DNA",
    instructions="Search the local transcript corpus and retrieve bounded evidence; retrieve selected transcripts and validate/store DNA JSON supplied by the connected model. This server does not perform semantic analysis.",
    log_level="WARNING",
)


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "errors": [str(exc)],
            "semantic_interpretations_independently_verified": False}


@mcp.tool(description="Return source metadata and timestamped captions for one manifest-selected Script ID.")
def get_selected_transcript(script_id: str) -> dict[str, Any]:
    try:
        return service.get_selected_transcript(script_id)
    except (KeyError, FileNotFoundError, OSError, ValueError) as exc:
        return _error(exc)


@mcp.tool(description="Return the Dynamic Script DNA client analysis instructions.")
def get_analysis_instructions() -> str:
    return service.get_analysis_instructions()


@mcp.tool(description="Return the versioned Dynamic Script DNA JSON Schema.")
def get_contract_schema() -> dict[str, Any]:
    return service.get_contract_schema()


@mcp.tool(description="Validate client-generated DNA JSON against source captions, evidence and timeline. Does not save.")
def validate_dna_json(script_id: str, dna_json: str) -> dict[str, Any]:
    return service.validate_dna_json(script_id, dna_json)


@mcp.tool(description="Save valid client-generated DNA JSON locally. Existing profiles are never overwritten.")
def save_validated_profile(script_id: str, dna_json: str) -> dict[str, Any]:
    return service.save_validated_profile(script_id, dna_json)


@mcp.tool(description="Retrieve a previously saved profile by manifest-selected Script ID.")
def get_stored_profile(script_id: str) -> dict[str, Any]:
    try:
        return {"found": True, "profile": service.get_stored_profile(script_id)}
    except (KeyError, FileNotFoundError, OSError, ValueError) as exc:
        return _error(exc)


@mcp.tool(description="Search all indexed local transcripts lexically; returns bounded results with source provenance. Scores are not storytelling quality.")
def search_transcripts(query: str = "", query_variants: list[str] | None = None,
                       exact_phrase: bool = False, transcript_id: str | None = None,
                       narrative_functions: list[str] | None = None,
                       limit: int = 10, offset: int = 0) -> dict[str, Any]:
    """Search corpus-wide without changing get_selected_transcript's manifest limit."""
    try:
        return corpus_search.search_transcripts(
            query=query, query_variants=query_variants, exact_phrase=exact_phrase,
            transcript_id=transcript_id, narrative_functions=narrative_functions,
            limit=limit, offset=offset)
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
        return _error(exc)


@mcp.tool(description="Return bounded exact caption evidence from any indexed local transcript after path/hash verification.")
def get_transcript_evidence(transcript_id: str,
                            caption_ids: list[str] | None = None,
                            query: str | None = None,
                            max_captions: int = 6, max_chars: int = 1200) -> dict[str, Any]:
    """Read bounded caption evidence; this separate tool is not manifest-restricted."""
    try:
        return corpus_evidence.get_transcript_evidence(
            transcript_id=transcript_id, caption_ids=caption_ids, query=query,
            max_captions=max_captions, max_chars=max_chars)
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
        return _error(exc)


@mcp.tool(description="Search Writer DNA lexically; by default returns only explicitly approved C templates.")
def search_writer_dna(query: str = "", category: str | None = None,
                      review_status: str = "approved", transcript_id: str | None = None,
                      dna_id: str | None = None, limit: int = 10) -> dict[str, Any]:
    try:
        return writer_dna_library.search(query, category, review_status,
                                         transcript_id, dna_id, limit)
    except (OSError, sqlite3.Error, ValueError) as exc:
        return _error(exc)


@mcp.tool(description="Retrieve a Writer DNA record by exact deterministic DNA ID, including provenance and constraints.")
def get_writer_dna(dna_id: str) -> dict[str, Any]:
    try:
        record = writer_dna_library.get(dna_id)
        return {"found": record is not None, "record": record}
    except (OSError, sqlite3.Error, ValueError) as exc:
        return _error(exc)


@mcp.tool(description="Validate and persist a model-proposed B candidate. Status is forced to pending; this tool cannot approve.")
def submit_writer_dna_candidate(candidate_json: str) -> dict[str, Any]:
    try:
        candidate = json.loads(candidate_json)
        if not isinstance(candidate, dict) or candidate.get("state") != "B":
            raise ValueError("only an analyzed B candidate can be submitted")
        candidate["review_status"] = "pending"
        return writer_dna_library.add_record(candidate)
    except (json.JSONDecodeError, OSError, sqlite3.Error, ValueError) as exc:
        return _error(exc)


if __name__ == "__main__":
    mcp.run(transport="stdio")
