from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from factoholic_script_engine.engine import ScriptEngine


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT_ID = "0001_Bo5azL9dpMM"
HOOK_QUOTE = "यह है दुनिया का सबसे महंगा स्कूल जिसकी साल भर की फीस सुनके तुम्हारी फट जाएगी।"


@pytest.mark.asyncio
async def test_actual_stdio_server_end_to_end_against_real_corpus(full_engine: ScriptEngine) -> None:
    env = os.environ.copy()
    env.update({
        "FACTOHOLIC_DATASET_DIR": str(ROOT / "scripts"),
        "FACTOHOLIC_STATE_DIR": str(full_engine.config.state_dir),
        "FACTOHOLIC_MASTER_INSTRUCTIONS": str(ROOT / "MASTER_INSTRUCTIONS.md"),
        "FACTOHOLIC_WORKFLOW_CONTRACT": str(ROOT / "WORKFLOW_CONTRACT.md"),
        "PYTHONIOENCODING": "utf-8",
    })
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "factoholic_script_engine.server"],
        cwd=ROOT,
        env=env,
    )
    async with Client(params, read_timeout_seconds=90) as client:
        tools = await client.list_tools()
        tool_names = {item.name for item in tools.tools}
        assert {"get_instructions", "search_references", "read_transcript", "save_outline", "approve_outline", "save_script", "validate_script", "get_audit_report", "get_corpus_learning_status", "get_next_corpus_learning_batch", "submit_corpus_learning_analysis", "validate_corpus_learning_analysis", "search_corpus_learning", "pause_corpus_learning_batch", "resume_corpus_learning_batch"} <= tool_names
        templates = await client.list_resource_templates()
        assert any(item.uri_template == "factoholic://transcript/{transcript_id}" for item in templates.resource_templates)

        started = await client.call_tool("create_session", {"topic": "Swiss boarding school fees", "operation": "script", "format": "short", "target_duration_seconds": 20})
        session = started.structured_content
        assert session["ok"] is True
        session_id = session["session_id"]
        assert session["master_instructions"]["content"].startswith("1. ROLE")
        cycle = session["corpus_learning_cycle"]
        assert cycle["purpose"] == "script"
        status = await client.call_tool("get_corpus_learning_status", {"batch_id": cycle["batch_id"]})
        total = status.structured_content["total_corpus_files_at_start"]
        assert total >= 900

        search = await client.call_tool("search_references", {"query": "expensive school", "limit": 5, "session_id": session_id})
        assert search.structured_content["results"][0]["transcript_id"] == TRANSCRIPT_ID
        language = await client.call_tool("search_language_patterns", {"query": "सबसे महंगा स्कूल", "exact_phrase": True, "session_id": session_id})
        assert language.structured_content["results"][0]["timestamp"] == "00:00:02.639"
        humor = await client.call_tool("search_humor_patterns", {"query": "स्कूल", "limit": 5})
        assert humor.structured_content["attribution_status"] == "required"

        read = await client.call_tool("read_transcript", {"transcript_id": TRANSCRIPT_ID, "session_id": session_id})
        assert read.structured_content["whole_transcript_returned"] is True
        reference = {"transcript_id": TRANSCRIPT_ID, "quote": HOOK_QUOTE, "timestamp": "00:00:02.639"}
        evidence = await client.call_tool("validate_reference_evidence", {"references": [reference]})
        assert evidence.structured_content["ok"] is True
        batch = await client.call_tool("get_next_corpus_learning_batch", {"batch_id": cycle["batch_id"], "max_transcripts": 1})
        delivered = batch.structured_content
        assert delivered["items_in_batch"] == 1
        assert delivered["items"][0]["original_markdown"]
        first_id = delivered["items"][0]["transcript_id"]
        await client.call_tool("pause_corpus_learning_batch", {"batch_id": cycle["batch_id"]})
        await client.call_tool("resume_corpus_learning_batch", {"batch_id": cycle["batch_id"]})
        redelivered = await client.call_tool("get_next_corpus_learning_batch", {"batch_id": cycle["batch_id"], "max_transcripts": 1})
        assert redelivered.structured_content["items"][0]["transcript_id"] == first_id
        coverage = await client.call_tool("get_corpus_learning_status", {"batch_id": cycle["batch_id"]})
        assert coverage.structured_content["delivered_transcripts"] == 1
        assert coverage.structured_content["ai_reported_inspected_transcripts"] == 0

        blocked_script = await client.call_tool("save_script", {
            "session_id": session_id, "content": "Premature output", "references": [], "fallback_reason": "Gate verification only",
        })
        assert blocked_script.structured_content["error"]["code"] == "full_corpus_reading_incomplete"

        outline_session_result = await client.call_tool("create_session", {"topic": "Swiss boarding school fees", "operation": "outline"})
        outline_session = outline_session_result.structured_content
        blocked_outline = await client.call_tool("save_outline", {
            "session_id": outline_session["session_id"], "content": "Premature outline", "references": [], "fallback_reason": "Gate verification only",
        })
        assert blocked_outline.structured_content["error"]["code"] == "full_corpus_reading_incomplete"
        audit = await client.call_tool("get_audit_report", {"session_id": session_id})
        assert audit.structured_content["corpus_learning"]["cycles"][0]["total_corpus_files_at_start"] == total
