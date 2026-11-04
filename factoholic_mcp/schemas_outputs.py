from __future__ import annotations
from .schemas_inputs import *

class CommonResult(BaseModel):
    status: Literal["ok", "error"] = "ok"
    error_code: str | None = None
    message: str | None = None


class PatternSummary(BaseModel):
    source_phase: Literal["phase3", "phase4"]
    pattern_id: str
    version: str
    name: str
    mechanism: str
    narrative_function: str
    topic_tags: list[str] = Field(default_factory=list)
    frequency: float | None = None
    confidence: float | str | None = None
    evidence_status: str
    support_count: int | None = None
    counterexample_count: int | None = None
    originality_restrictions: str


class DecisionRuleSummary(BaseModel):
    decision_id: str
    mechanism: str
    intended_function: str
    appropriate_context: str
    inappropriate_context: str
    confidence: float
    evidence_status: str
    originality_restrictions: str


class EvidenceSummary(BaseModel):
    status: Literal["not_supplied", "user_supplied_unverified"]
    fact_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    low_confidence_fact_ids: list[str] = Field(default_factory=list)
    freshness_warning_fact_ids: list[str] = Field(default_factory=list)
    semantic_verification_performed: bool = False


class SearchPatternsResult(CommonResult):
    query_terms: list[str] = Field(default_factory=list)
    patterns: list[PatternSummary] = Field(default_factory=list)
    decision_rules: list[DecisionRuleSummary] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class TopicAnalysisResult(CommonResult):
    topic: str | None = None
    audience: str | None = None
    duration_seconds: int | None = None
    language_preference: str | None = None
    tone: str | None = None
    evidence: EvidenceSummary = Field(default_factory=lambda: EvidenceSummary(status="not_supplied"))
    patterns: list[PatternSummary] = Field(default_factory=list)
    decision_rules: list[DecisionRuleSummary] = Field(default_factory=list)
    context_constraints: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class StoryAngle(BaseModel):
    angle_id: str
    framing: str
    evidence_fact_ids: list[str]
    pattern_ids: list[str] = Field(default_factory=list)
    caveat: str


class StoryAnglesResult(CommonResult):
    topic: str | None = None
    angles: list[StoryAngle] = Field(default_factory=list)
    available_pattern_ids: list[str] = Field(default_factory=list)
    evidence: EvidenceSummary = Field(default_factory=lambda: EvidenceSummary(status="not_supplied"))
    limitations: list[str] = Field(default_factory=list)


class PlanBeat(BaseModel):
    beat_id: str
    role: str
    evidence_fact_ids: list[str] = Field(default_factory=list)


class StoryPlanResult(CommonResult):
    topic: str | None = None
    selected_angle_id: str | None = None
    opening_question: str | None = None
    target_duration_seconds: int | None = None
    beats: list[PlanBeat] = Field(default_factory=list)
    ordered_fact_ids: list[str] = Field(default_factory=list)
    unassigned_fact_ids: list[str] = Field(default_factory=list)
    selected_pattern_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class OriginalityResult(CommonResult):
    lexical_status: str | None = None
    longest_exact_run_tokens: int = 0
    matching_video_count: int = 0
    matched_fraction: float = 0.0
    near_match_score: float = 0.0
    matched_evidence_video_id: str | None = None
    corpus_file_count: int = 0
    limitations: str = ""
    human_semantic_review_required: bool = True


class ClaimBindingResult(BaseModel):
    claim_id: str
    supplied_fact_ids: list[str] = Field(default_factory=list)
    unknown_fact_ids: list[str] = Field(default_factory=list)
    binding_status: Literal["bound_by_id_only", "unbound", "unknown_fact_id"]


class RubricDimension(BaseModel):
    dimension_id: str
    question: str
    score_anchors: dict[str, str]


class EvaluationResult(CommonResult):
    topic: str | None = None
    word_count: int = 0
    estimated_spoken_seconds: float = 0.0
    target_duration_seconds: int | None = None
    duration_check: str | None = None
    originality: OriginalityResult | None = None
    claim_bindings: list[ClaimBindingResult] = Field(default_factory=list)
    unreferenced_fact_ids: list[str] = Field(default_factory=list)
    rubric_for_chatgpt: list[RubricDimension] = Field(default_factory=list)
    factual_gate: Literal["not_semantically_assessed"] = "not_semantically_assessed"
    quality_scores: None = None
    limitations: list[str] = Field(default_factory=list)


class ImprovementAction(BaseModel):
    action_id: str
    priority: Literal["high", "medium", "low"]
    instruction: str
    evidence_fact_ids: list[str] = Field(default_factory=list)


class ImproveScriptResult(CommonResult):
    evaluation: EvaluationResult | None = None
    action_items: list[ImprovementAction] = Field(default_factory=list)
    preserve: list[str] = Field(default_factory=list)
    rewrite_performed_by_tool: bool = False
    rewrite_owner: str = "ChatGPT uses this evidence-aware brief to write the revision."
    limitations: list[str] = Field(default_factory=list)
