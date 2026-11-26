from __future__ import annotations

from pathlib import Path

import pytest

from factoholic_script_engine.config import EngineConfig
from factoholic_script_engine.engine import EngineError, ScriptEngine


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT_ID = "0001_Bo5azL9dpMM"
HOOK_QUOTE = "यह है दुनिया का सबसे महंगा स्कूल जिसकी साल भर की फीस सुनके तुम्हारी फट जाएगी।"
FEE_QUOTE = "क्योंकि स्कूल की फीस भी तो लगभग 1 करोड़"


def make_engine(state: Path, creator: str | None = None) -> ScriptEngine:
    return ScriptEngine(EngineConfig(
        dataset_dir=ROOT / "scripts",
        state_dir=state,
        instructions_file=ROOT / "MASTER_INSTRUCTIONS.md",
        workflow_file=ROOT / "WORKFLOW_CONTRACT.md",
        dataset_creator=creator,
    ))


def make_small_engine(tmp_path: Path) -> ScriptEngine:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    source = (ROOT / "scripts" / f"{TRANSCRIPT_ID}.md").read_text(encoding="utf-8")
    # Keep the real source captions while omitting its appended, pre-existing analysis.
    source = source.split("[AI-ANALYSIS: BEGIN]", 1)[0]
    (dataset / f"{TRANSCRIPT_ID}.md").write_text(source, encoding="utf-8")
    return ScriptEngine(EngineConfig(
        dataset_dir=dataset,
        state_dir=tmp_path / "state",
        instructions_file=ROOT / "MASTER_INSTRUCTIONS.md",
        workflow_file=ROOT / "WORKFLOW_CONTRACT.md",
    ))


TOPIC_ANALYSIS = {
    "topic_relevance": {"current_topic": "Swiss boarding school fees", "observations": ["Use a fee curiosity gap for this topic."]},
    "language": {"observations": ["Notice simple audience-facing wording."]},
    "hooks_and_frames": {"observations": ["Open with a direct question."]},
    "humor": {"not_applicable_reason": "No humor pattern is being submitted from this source."},
    "explanation": {"observations": ["Delay the amount until the context is clear."]},
    "relatable_dialogue": {"observations": ["A viewer-facing question is suitable."]},
    "storytelling": {"observations": ["Resolve the opening curiosity with the amount."]},
    "script_dna": {"observations": ["No appended DNA exists in this test copy."]},
}


def process_cycle(engine: ScriptEngine, session_id: str, purpose: str, cycle: dict | None = None) -> tuple[str, str]:
    if cycle is None:
        cycle = engine.start_corpus_learning_batch(session_id, purpose)
    batch_id = cycle["batch_id"]
    delivery = engine.get_next_corpus_learning_batch(batch_id, max_transcripts=1)
    item = delivery["items"][0]
    assert item["original_markdown"] and item["delivery_chars"] > len(item["original_markdown"])
    # Simulate a lost response: redelivery must not inflate unique transcript coverage.
    redelivery = engine.get_next_corpus_learning_batch(batch_id, max_transcripts=1)
    assert redelivery["items"][0]["transcript_id"] == item["transcript_id"]
    status = engine.get_corpus_learning_status(batch_id)
    assert status["delivered_transcripts"] == 1
    pattern = {
        "bank_category": "hook_starter", "pattern": "Direct question opens a curiosity gap",
        "reference_does": "The opening frames an unusually expensive school as a question worth resolving.",
        "why_works": "A concrete unanswered question gives the viewer a reason to keep listening.",
        "principle": "Create a specific information gap, then resolve it with evidence.",
        "function": "Hook and reveal", "suitable_applications": ["Explaining a surprising price with context first."],
        "avoid_applications": ["Topics where a question would imply a misleading claim."],
        "original_equivalent_method": "Ask an original, accurate question about the current topic and answer it promptly.",
        "evidence_quotes": [{"quote": HOOK_QUOTE, "timestamp": "00:00:02.639"}],
        "learning_level": "creative_adaptation",
    }
    submitted = engine.submit_corpus_learning_analysis(
        batch_id, item["transcript_id"], True, "A fee curiosity hook delays the reveal.",
        {"status": "not_present", "claims": []}, [pattern], TOPIC_ANALYSIS,
    )
    assert submitted["ok"] is True
    validated = engine.validate_corpus_learning_analysis(batch_id, item["transcript_id"])
    assert validated["status"] == "evidence_validated"
    assert engine.get_corpus_learning_status(batch_id)["status"] == "completed"
    pattern_id = engine.search_corpus_learning(facets={"hooks": "question curiosity"}, session_id=session_id)["patterns"][0]["pattern_id"]
    for key in ("language_bank", "humor_opportunity_map", "storytelling_plan"):
        engine.update_session_context(session_id, key, {
            "reading_cycle_id": batch_id, "topic": "Swiss boarding school fees",
            "pattern_ids": [pattern_id], "application": "Use the opening question, then answer it with verified context.",
        })
    return batch_id, pattern_id


def test_evidence_analysis_outline_approval_script_and_audit(tmp_path: Path) -> None:
    engine = make_small_engine(tmp_path)
    session = engine.create_session("Swiss boarding school fees", "script", "short", 20)
    session_id = session["session_id"]
    assert session["master_instructions"]["content"].startswith("1. ROLE")

    found = engine.search_references("expensive school", limit=5, session_id=session_id)
    assert found["results"][0]["transcript_id"] == TRANSCRIPT_ID
    transcript = engine.read_transcript(TRANSCRIPT_ID, session_id=session_id)
    assert transcript["whole_transcript_returned"] is True
    assert transcript["complete"] is True
    assert "00:00:02.639" in transcript["content"]
    dna = engine.get_script_dna(TRANSCRIPT_ID)
    assert len(dna["analysis_framework"]) == 18
    assert dna["analysis_framework"][0]["dimension"] == "Hook Type"

    evidence = {"transcript_id": TRANSCRIPT_ID, "quote": HOOK_QUOTE, "timestamp": "00:00:02.639"}
    validation = engine.validate_reference_evidence([evidence])
    assert validation["ok"] is True
    rejected_dna = engine.save_reference_analysis(
        TRANSCRIPT_ID,
        {"script_dna": {"Hook Type": {"observation": "Unsupported claim", "evidence_quotes": [{"quote": "not in this transcript"}]}}},
        [], session_id,
    )
    assert rejected_dna["ok"] is False
    analysis = engine.save_reference_analysis(
        TRANSCRIPT_ID,
        {
            "summary": "A fee curiosity gap is established before details.",
            "script_dna": {
                "Hook Type": {
                    "observation": "The opening presents the school as unusually expensive.",
                    "evidence_quotes": [{"quote": HOOK_QUOTE, "timestamp": "00:00:02.639"}],
                    "estimate": False,
                },
            },
        },
        [{
            "category": "hook", "pattern": "Curiosity-first fee hook", "function": "Open a question about the fee before explaining the school.",
            "explanation": "The narration withholds the amount until later.", "evidence_quote": HOOK_QUOTE,
            "timestamp": "00:00:02.639", "learning_level": "creative_adaptation",
        }],
        session_id,
    )
    assert analysis["ok"] is True
    assert analysis["verified_script_dna_quotes"] == 1
    assert analysis["semantic_status"] == "model_semantics_not_independently_verified"
    # The legacy reference bank remains available, but the new mandatory cycle is independent.
    assert engine.get_language_bank(session_id=session_id)["returned"] == 1
    outline_first = engine.start_corpus_learning_batch(session_id, "outline")
    with pytest.raises(EngineError, match="every transcript"):
        engine.save_outline(session_id, "Premature outline", [evidence], duration_estimate_seconds=20)
    outline_cycle, outline_pattern = process_cycle(engine, session_id, "outline", outline_first)
    outline = engine.save_outline(
        session_id,
        "Hook: school fees. Explain the Swiss school and its amenities. Reveal the reported annual fee and resolve the opening question.",
        [evidence],
        duration_estimate_seconds=20,
    )
    assert outline["ok"] is True
    assert outline["status"] == "pending_approval"
    assert engine.get_approved_outline(session_id)["returned"] == 0
    with pytest.raises(EngineError, match="user's explicit approval statement"):
        engine.approve_outline(session_id, outline["outline_id"], "")
    approved = engine.approve_outline(session_id, outline["outline_id"], "Approved for this workflow test.")
    assert approved["status"] == "approved"
    assert engine.get_approved_outline(session_id, outline["outline_id"])["returned"] == 1

    script_cycle, script_pattern = process_cycle(engine, session_id, "script")
    assert script_cycle != outline_cycle

    script = (
        "क्या आप अपने स्कूल की फीस को ज्यादा मानते हैं? इस स्विस बोर्डिंग स्कूल की सालाना फीस लगभग एक करोड़ तीस लाख रुपये बताई गई है। "
        "यहां खेल, संगीत और कई सुविधाएं हैं। यानी महंगी फीस के साथ पढ़ाई के अलावा भी बहुत कुछ मिलता है।"
    )
    refs = [evidence, {"transcript_id": TRANSCRIPT_ID, "quote": FEE_QUOTE, "timestamp": "00:00:51.440"}]
    saved = engine.save_script(
        session_id, script, refs, outline_id=outline["outline_id"],
        mappings=[{"learning_pattern_id": script_pattern, "narration_excerpt": "क्या आप अपने स्कूल की फीस को ज्यादा मानते हैं?"}],
    )
    assert saved["ok"] is True
    validation_result = engine.validate_script(script_id=saved["script_id"])
    assert validation_result["passed_deterministic_checks"] is True
    report = engine.get_audit_report(session_id)
    assert report["reference_coverage"]["fully_inspected_count"] == 1
    assert report["reference_coverage"]["actual_files"][0]["filename"] == "0001_Bo5azL9dpMM.md"
    assert report["outlines"][0]["status"] == "approved"
    assert report["scripts"][0]["word_count"] == len(script.split())
    assert len(report["corpus_learning"]["cycles"]) == 3
    assert report["corpus_learning"]["cycles"][-1]["evidence_validated_analyses"] == 1


def test_humor_search_requires_creator_provenance_then_uses_explicit_configuration(full_engine: ScriptEngine) -> None:
    unconfigured = make_engine(full_engine.config.state_dir)
    blocked = unconfigured.search_humor_patterns("स्कूल", limit=5)
    assert blocked["attribution_status"] == "required"
    assert blocked["returned"] == 0

    configured = make_engine(full_engine.config.state_dir, creator="FactoHolic")
    results = configured.search_humor_patterns("स्कूल", limit=5)
    assert results["attribution_status"] == "operator_configured"
    assert results["returned"] >= 1
    assert results["results"][0]["attribution_basis"] == "operator_configured_dataset_creator"
    assert results["results"][0]["evidence_status"] == "caption_text_retrieved; humor_quality_not_assessed"


def test_instruction_hash_reload_and_session_isolation(full_engine: ScriptEngine) -> None:
    engine = full_engine
    first = engine.create_session("First topic", "outline")
    second = engine.create_session("Second topic", "research")
    assert first["session_id"] != second["session_id"]
    assert engine.get_session_context(first["session_id"])["session"]["topic"] == "First topic"
    engine.update_session_context(first["session_id"], "research_context", {"note": "first only"})
    assert "research_context" in engine.get_session_context(first["session_id"])["context"]
    assert "research_context" not in engine.get_session_context(second["session_id"])["context"]


def test_restart_recovery_and_operation_guards(full_engine: ScriptEngine) -> None:
    engine = full_engine
    session = engine.create_session("Restart recovery", "research")
    session_id = session["session_id"]
    engine.update_session_context(session_id, "research_context", {"sources": [TRANSCRIPT_ID]})
    restarted = make_engine(engine.config.state_dir)
    resumed = restarted.get_session_context(session_id)
    assert resumed["context"]["research_context"]["value"] == {"sources": [TRANSCRIPT_ID]}
    assert resumed["session"]["operation"] == "research"

    with pytest.raises(EngineError, match="requires an outline or script request"):
        restarted.save_outline(session_id, "A research outline", [], fallback_reason="Not requested")
    with pytest.raises(EngineError, match="explicit script request"):
        restarted.save_script(session_id, "A script", [], fallback_reason="Not requested")
    with pytest.raises(EngineError, match="transcript ID"):
        restarted.read_transcript("../MASTER_INSTRUCTIONS")


def test_instruction_change_requires_refresh_before_outline_save(tmp_path: Path) -> None:
    custom_instructions = tmp_path / "instructions.md"
    custom_instructions.write_text("First instructions", encoding="utf-8")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    source = (ROOT / "scripts" / f"{TRANSCRIPT_ID}.md").read_text(encoding="utf-8").split("[AI-ANALYSIS: BEGIN]", 1)[0]
    (dataset / f"{TRANSCRIPT_ID}.md").write_text(source, encoding="utf-8")
    engine = ScriptEngine(EngineConfig(
        dataset_dir=dataset,
        state_dir=tmp_path / "state",
        instructions_file=custom_instructions,
        workflow_file=ROOT / "WORKFLOW_CONTRACT.md",
    ))
    session = engine.create_session("Instruction check", "outline")
    custom_instructions.write_text("Changed instructions", encoding="utf-8")
    with pytest.raises(EngineError, match="changed during this session"):
        engine.save_outline(session["session_id"], "An outline", [], fallback_reason="No references available")
    refreshed = engine.refresh_session_instructions(session["session_id"])
    assert refreshed["master_instructions"]["content"] == "Changed instructions"
    with pytest.raises(EngineError, match="belongs to another operation or instruction version"):
        engine.save_outline(session["session_id"], "An outline", [], fallback_reason="No references available")
    engine.start_corpus_learning_batch(session["session_id"], "outline")
    with pytest.raises(EngineError, match="every transcript"):
        engine.save_outline(session["session_id"], "An outline", [], fallback_reason="No references available")


def test_context_limit_missing_source_and_stale_cycle_are_reported(tmp_path: Path) -> None:
    engine = make_small_engine(tmp_path)
    source_path = engine.config.dataset_dir / f"{TRANSCRIPT_ID}.md"
    source_path.write_text(source_path.read_text(encoding="utf-8") + "\n\n<!-- context limit fixture -->\n" + ("x" * 6000), encoding="utf-8")
    engine.refresh_index()
    session = engine.create_session("Swiss boarding school fees", "outline")
    cycle_id = session["corpus_learning_cycle"]["batch_id"]

    too_small = engine.get_next_corpus_learning_batch(cycle_id, max_transcripts=1, max_chars=5000)
    assert too_small["ok"] is False
    assert too_small["error"]["code"] == "transcript_exceeds_client_batch_limit"
    assert engine.get_corpus_learning_status(cycle_id)["delivered_transcripts"] == 0

    source_path.unlink()
    engine.refresh_index()
    missing = engine.get_next_corpus_learning_batch(cycle_id, max_transcripts=1, max_chars=10000)
    assert missing["next_action"] == "resolve_review_or_retry"
    status = engine.get_corpus_learning_status(cycle_id)
    assert status["coverage_stale"] is True
    assert status["counts"]["requires_review"] == 1
    with pytest.raises(EngineError, match="every transcript"):
        engine.save_outline(session["session_id"], "Do not save", [], fallback_reason="Blocked-source verification")
