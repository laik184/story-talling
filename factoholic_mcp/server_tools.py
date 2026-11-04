"""MCP tool registration for the local stdio server."""
from __future__ import annotations

import argparse
import logging
from typing import Annotated, Any, Callable, TypeVar

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .schemas import (
    ClaimEvidenceInput,
    CommonResult,
    EvaluationResult,
    EvidencePackInput,
    ImproveScriptResult,
    OriginalityResult,
    SearchPatternsResult,
    StoryAnglesResult,
    StoryPlanResult,
    TopicAnalysisResult,
    TopicContextInput,
)
from .service import ServiceError, StorytellingService


LOGGER = logging.getLogger("factoholic_mcp")
T = TypeVar("T", bound=CommonResult)

SERVER_INSTRUCTIONS = """FactoHolic Storytelling tools provide generalized pattern records and deterministic editorial analysis.
The local transcript corpus is private: never request or expect source transcript text, titles, examples,
or distinctive creator expressions. Supplied evidence is caller-provided and is not semantically verified.
Use ChatGPT for reasoning and any script rewrite; the server makes no paid model calls. Originality
screening is lexical only and requires human semantic review. All tools are read-only and do not predict views."""


def _error_result(result_type: type[T], code: str, message: str) -> T:
    return result_type(status="error", error_code=code, message=message)


def _safe_call(result_type: type[T], callback: Callable[[], T]) -> T:
    try:
        return callback()
    except ServiceError as exc:
        return _error_result(result_type, exc.code, exc.public_message)
    except (ValueError, TypeError) as exc:
        # Validation messages may include caller-provided text; keep them out of logs and outputs.
        LOGGER.info("tool input rejected (%s)", type(exc).__name__)
        return _error_result(result_type, "invalid_input", "The supplied input is invalid or inconsistent.")
    except Exception as exc:  # Fail closed without logging user text, script text, or credentials.
        LOGGER.error("tool failed (%s)", type(exc).__name__)
        return _error_result(result_type, "internal_error", "The tool could not complete this request.")


def create_server(service: StorytellingService | None = None) -> MCPServer:
    service = service or StorytellingService()
    server = MCPServer(
        name="factoholic-storytelling",
        title="FactoHolic Storytelling Intelligence",
        version="0.1.0",
        instructions=SERVER_INSTRUCTIONS,
        log_level="WARNING",
    )
    safe_annotations = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    @server.tool(annotations=safe_annotations, structured_output=True)
    def search_story_patterns(
        query: Annotated[str, Field(min_length=1, max_length=500, description="Short topic or search terms (maximum 500 characters).")],
        context: TopicContextInput | None = None,
    ) -> SearchPatternsResult:
        """Use when you need reusable, generalized story structures for a topic. Never returns transcript text or titles."""
        return _safe_call(SearchPatternsResult, lambda: service.search_story_patterns(query, context))

    @server.tool(annotations=safe_annotations, structured_output=True)
    def analyze_topic(
        topic: Annotated[str, Field(min_length=1, max_length=1_000, description="Topic to analyze (maximum 1,000 characters).")],
        evidence: EvidencePackInput | None = None,
                      context: TopicContextInput | None = None) -> TopicAnalysisResult:
        """Use to map a topic to generalized patterns and inspect optional source-linked evidence metadata; claims are not verified."""
        return _safe_call(TopicAnalysisResult, lambda: service.analyze_topic(topic, evidence, context))

    @server.tool(annotations=safe_annotations, structured_output=True)
    def suggest_story_angles(
        topic: Annotated[str, Field(min_length=1, max_length=1_000, description="Topic to frame (maximum 1,000 characters).")],
        evidence: EvidencePackInput,
                             context: TopicContextInput | None = None) -> StoryAnglesResult:
        """Use after collecting source-linked facts to get generic story frames bound to those fact IDs."""
        return _safe_call(StoryAnglesResult, lambda: service.suggest_story_angles(topic, evidence, context))

    @server.tool(annotations=safe_annotations, structured_output=True)
    def build_story_plan(
        topic: Annotated[str, Field(min_length=1, max_length=1_000, description="Topic for this outline (maximum 1,000 characters).")],
        selected_angle_id: Annotated[str, Field(description="Angle ID from suggest_story_angles: question_to_evidence, mechanism_chain, or boundary_then_payoff.")],
        opening_question: Annotated[str, Field(min_length=1, max_length=500, description="Caller-written opening question; this is not fact-checked.")],
        fact_order: Annotated[list[Annotated[str, Field(min_length=1, max_length=100)]], Field(min_length=1, max_length=50, description="Unique fact IDs from the supplied evidence pack, in narrative order.")],
        evidence: EvidencePackInput,
        selected_pattern_ids: Annotated[list[str] | None, Field(max_length=18, description="Optional IDs from the current pattern search, up to 18.")] = None,
                         context: TopicContextInput | None = None) -> StoryPlanResult:
        """Use after selecting a frame to build a short beat outline linked to supplied evidence IDs."""
        return _safe_call(StoryPlanResult, lambda: service.build_story_plan(
            topic, selected_angle_id, opening_question, fact_order, evidence, selected_pattern_ids, context
        ))

    @server.tool(annotations=safe_annotations, structured_output=True)
    def evaluate_script(
        topic: Annotated[str, Field(min_length=1, max_length=1_000, description="Topic for the submitted draft (maximum 1,000 characters).")],
        script: Annotated[str, Field(min_length=1, max_length=20_000, description="Draft to measure and screen locally; maximum 20,000 characters.")],
        target_duration_seconds: Annotated[int, Field(ge=15, le=180, description="Desired spoken duration from 15 to 180 seconds.")],
        evidence: EvidencePackInput | None = None,
        claim_map: Annotated[list[ClaimEvidenceInput] | None, Field(max_length=50, description="Optional claim-to-fact ID links, up to 50 claims.")] = None,
    ) -> EvaluationResult:
        """Use to estimate duration, inspect structural evidence-ID links, and return ChatGPT's scoring rubric; no quality score is generated."""
        return _safe_call(EvaluationResult, lambda: service.evaluate_script(
            topic, script, target_duration_seconds, evidence, claim_map
        ))

    @server.tool(annotations=safe_annotations, structured_output=True)
    def check_originality(
        script: Annotated[str, Field(min_length=1, max_length=20_000, description="Draft to compare locally; maximum 20,000 characters.")],
    ) -> OriginalityResult:
        """Use for a local lexical overlap screen. Results contain no source passages and do not prove semantic originality."""
        return _safe_call(OriginalityResult, lambda: service.scan_originality(script))

    @server.tool(annotations=safe_annotations, structured_output=True)
    def improve_script(
        topic: Annotated[str, Field(min_length=1, max_length=1_000, description="Topic for the submitted draft (maximum 1,000 characters).")],
        script: Annotated[str, Field(min_length=1, max_length=20_000, description="Draft to assess; maximum 20,000 characters.")],
        target_duration_seconds: Annotated[int, Field(ge=15, le=180, description="Desired spoken duration from 15 to 180 seconds.")],
        evidence: EvidencePackInput | None = None,
        claim_map: Annotated[list[ClaimEvidenceInput] | None, Field(max_length=50, description="Optional claim-to-fact ID links, up to 50 claims.")] = None,
    ) -> ImproveScriptResult:
        """Use to get prioritized deterministic edits; this tool never writes the replacement script."""
        return _safe_call(ImproveScriptResult, lambda: service.improve_script(
            topic, script, target_duration_seconds, evidence, claim_map
        ))

    return server



