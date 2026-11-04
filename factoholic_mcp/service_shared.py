"""Read-only, model-free adapter over the existing Phase 3–6 components."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENGINE_DIR = ROOT / "04_creative_engine"
RESEARCH_DIR = ROOT / "02_research_engine"
for _directory in (ENGINE_DIR, RESEARCH_DIR):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

from models import EvidenceBundle, EvidenceFact, EvidenceSource, PatternContext, TopicRequest, ValidationError  # noqa: E402
from originality import CorpusOriginalityScanner, tokens  # noqa: E402
from research import freshness_issues  # noqa: E402
from retrieval import PatternRetriever  # noqa: E402
from .schemas import (  # noqa: E402
    ClaimBindingResult,
    ClaimEvidenceInput,
    DecisionRuleSummary,
    EvidenceFactInput,
    EvidencePackInput,
    EvidenceSummary,
    EvaluationResult,
    ImprovementAction,
    ImproveScriptResult,
    OriginalityResult,
    PatternSummary,
    PlanBeat,
    RubricDimension,
    SearchPatternsResult,
    StoryAngle,
    StoryAnglesResult,
    StoryPlanResult,
    TopicAnalysisResult,
    TopicContextInput,
)


LOGGER = logging.getLogger("factoholic_mcp.service")
RUBRIC_PATH = ROOT / "05_evaluation" / "rubrics" / "phase6_storytelling_rubric_v1.json"
DATASET_DIR = ROOT / "01_dataset" / "raw_transcripts" / "FactoHolic_Top_1000_Accessible_Transcripts"
PHASE4_VERSION = "4.0.12"
WORDS_PER_MINUTE = 135
MAX_TOPIC_CHARS = 1_000
MAX_SCRIPT_CHARS = 20_000
MAX_QUERY_CHARS = 500


class ServiceError(ValueError):
    def __init__(self, code: str, public_message: str):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


def _context(value: TopicContextInput | None) -> PatternContext | None:
    if value is None:
        return None
    data = value.model_dump()
    data["hook_requirements"] = tuple(data["hook_requirements"])
    return PatternContext(**data)


def _fact_dicts(evidence: EvidencePackInput | None) -> list[dict[str, Any]]:
    if evidence is None:
        return []
    # Keep retrieval queries bounded; claims themselves are never returned by MCP tools.
    return [
        {"fact_id": fact.fact_id, "claim": fact.claim[:300], "confidence": fact.confidence}
        for fact in evidence.facts[:12]
    ]


def _evidence_bundle(topic: str, evidence: EvidencePackInput | None) -> EvidenceBundle | None:
    if evidence is None:
        return None
    sources = tuple(
        EvidenceSource(
            source_id=source.source_id,
            title=source.title,
            publisher=source.publisher,
            url=source.url,
            source_type=source.source_type,
            accessed_at=source.accessed_at,
            evidence_excerpt=source.evidence_excerpt,
            limitations=source.limitations,
        )
        for source in evidence.sources
    )
    facts = tuple(
        EvidenceFact(
            fact_id=fact.fact_id,
            claim=fact.claim,
            source_ids=tuple(fact.source_ids),
            confidence=fact.confidence,
            time_sensitive=fact.time_sensitive,
        )
        for fact in evidence.facts
    )
    return EvidenceBundle(
        topic=topic,
        queries=(),
        sources=sources,
        facts=facts,
        open_questions=tuple(evidence.open_questions),
    )

__all__ = [name for name in globals() if not name.startswith('__')]
