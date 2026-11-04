from __future__ import annotations
from .service_shared import *

class QueryOperations:
    def search_story_patterns(self, query: str, context: TopicContextInput | None = None) -> SearchPatternsResult:
        if not query.strip() or len(query) > MAX_QUERY_CHARS:
            raise ServiceError("invalid_input", f"query must be 1–{MAX_QUERY_CHARS} characters")
        retrieved = self._retrieve(query.strip(), None, context)
        patterns = self._pattern_summaries(retrieved)
        return SearchPatternsResult(
            query_terms=list(retrieved.get("query_terms", [])),
            patterns=patterns,
            decision_rules=self._rule_summaries(retrieved),
            limitations=[
                "Patterns summarize descriptive corpus observations; frequency is not proof of audience performance.",
                "Only generalized mechanisms and IDs are returned; transcript text, titles, and examples are withheld.",
                str(retrieved.get("review_status", "")),
            ],
        )

    def analyze_topic(self, topic: str, evidence: EvidencePackInput | None = None,
                      context: TopicContextInput | None = None) -> TopicAnalysisResult:
        request = self._validate_request(topic, context)
        retrieved = self._retrieve(request.topic, evidence, context)
        return TopicAnalysisResult(
            topic=request.topic,
            audience=request.audience,
            duration_seconds=request.duration_seconds,
            language_preference=request.language_preference,
            tone=request.tone,
            evidence=self._evidence_summary(request.topic, evidence),
            patterns=self._pattern_summaries(retrieved),
            decision_rules=self._rule_summaries(retrieved),
            context_constraints=list(retrieved.get("context_constraints", [])),
            limitations=[
                "This tool validates supplied evidence structure and freshness metadata only; it does not verify claims against source pages.",
                "No model call is made and no sensitivity, complexity, or truth judgment is inferred unless explicitly supplied in context.",
                "Retrieved pattern records are provisional and do not predict views, retention, or performance.",
            ],
        )

    def suggest_story_angles(self, topic: str, evidence: EvidencePackInput,
                             context: TopicContextInput | None = None) -> StoryAnglesResult:
        request = self._validate_request(topic, context)
        if not evidence.facts:
            raise ServiceError("evidence_required", "Supply at least one source-linked fact before suggesting angles.")
        retrieved = self._retrieve(request.topic, evidence, context)
        pattern_ids = [pattern.pattern_id for pattern in self._pattern_summaries(retrieved)[:6]]
        fact_ids = [fact.fact_id for fact in evidence.facts]
        angles = [
            StoryAngle(
                angle_id="question_to_evidence",
                framing="Open with a listener question that the supplied fact IDs can answer; resolve it using only those facts.",
                evidence_fact_ids=fact_ids,
                pattern_ids=pattern_ids[:2],
                caveat="The tool does not invent a hook or decide which facts substantively support the question.",
            ),
            StoryAngle(
                angle_id="mechanism_chain",
                framing="Arrange the supplied facts as condition → mechanism → consequence, only where the source-linked facts establish that relationship.",
                evidence_fact_ids=fact_ids,
                pattern_ids=pattern_ids[2:4],
                caveat="The caller must verify the causal ordering against the source evidence.",
            ),
            StoryAngle(
                angle_id="boundary_then_payoff",
                framing="Pair a main evidence point with its limitation or uncertainty, then state the narrow conclusion those facts permit.",
                evidence_fact_ids=fact_ids,
                pattern_ids=pattern_ids[4:6],
                caveat="Do not turn a limitation, low-confidence fact, or absent evidence into a dramatic reveal.",
            ),
        ]
        return StoryAnglesResult(
            topic=request.topic,
            angles=angles,
            available_pattern_ids=pattern_ids,
            evidence=self._evidence_summary(request.topic, evidence),
            limitations=[
                "These are generalized planning frames, not finished script language or verified causal claims.",
                "Pattern IDs point to abstract records; no source transcript, title, quote, or example is returned.",
            ],
        )

    def build_story_plan(self, topic: str, selected_angle_id: str, opening_question: str,
                         fact_order: list[str], evidence: EvidencePackInput,
                         selected_pattern_ids: list[str] | None = None,
                         context: TopicContextInput | None = None) -> StoryPlanResult:
        request = self._validate_request(topic, context)
        allowed_angles = {"question_to_evidence", "mechanism_chain", "boundary_then_payoff"}
        if selected_angle_id not in allowed_angles:
            raise ServiceError("invalid_angle", "Select one angle ID returned by suggest_story_angles.")
        if not opening_question.strip() or len(opening_question) > 500:
            raise ServiceError("invalid_input", "opening_question must be 1–500 characters")
        known_fact_ids = {fact.fact_id for fact in evidence.facts}
        if not fact_order or len(fact_order) > 50 or len(fact_order) != len(set(fact_order)):
            raise ServiceError("invalid_fact_order", "fact_order must contain unique supplied fact IDs")
        unknown_facts = set(fact_order) - known_fact_ids
        if unknown_facts:
            raise ServiceError("unknown_fact_id", "fact_order contains an ID missing from the supplied evidence")
        retrieved = self._retrieve(request.topic, evidence, context)
        available_patterns = {p.pattern_id for p in self._pattern_summaries(retrieved)}
        chosen_patterns = selected_pattern_ids or []
        if len(chosen_patterns) > 18:
            raise ServiceError("invalid_pattern_ids", "selected_pattern_ids may contain at most 18 IDs")
        if len(chosen_patterns) != len(set(chosen_patterns)) or set(chosen_patterns) - available_patterns:
            raise ServiceError("unknown_pattern_id", "selected_pattern_ids must come from current pattern search results")

        facts_by_id = {fact.fact_id: fact for fact in evidence.facts}
        low_confidence = [fact_id for fact_id in fact_order if facts_by_id[fact_id].confidence == "low"]
        middle = fact_order[1:-1]
        beats = [
            PlanBeat(beat_id="opening_question", role="State the caller-supplied question without adding a factual assertion."),
            PlanBeat(beat_id="context", role="Introduce the first supplied fact as context.", evidence_fact_ids=[fact_order[0]]),
        ]
        if middle:
            beats.append(PlanBeat(beat_id="evidence_progression", role="Use the remaining ordered facts to explain the topic; preserve their source limits.", evidence_fact_ids=middle))
        if low_confidence:
            beats.append(PlanBeat(beat_id="qualification", role="State required caveats next to low-confidence facts.", evidence_fact_ids=low_confidence))
        beats.append(PlanBeat(beat_id="payoff", role="Answer the caller-supplied question with the final supplied fact; do not add a new claim.", evidence_fact_ids=[fact_order[-1]]))
        used_ids = {fact_id for beat in beats for fact_id in beat.evidence_fact_ids}
        return StoryPlanResult(
            topic=request.topic,
            selected_angle_id=selected_angle_id,
            opening_question=opening_question.strip(),
            target_duration_seconds=request.duration_seconds,
            beats=beats,
            ordered_fact_ids=fact_order,
            unassigned_fact_ids=sorted(known_fact_ids - used_ids),
            selected_pattern_ids=chosen_patterns,
            source_ids=sorted({sid for fact in evidence.facts if fact.fact_id in fact_order for sid in fact.source_ids}),
            limitations=[
                "This is an evidence-ID outline, not a script or a judgment that the facts support one another.",
                "Pattern guidance is provisional; source verification and human editorial review remain separate.",
            ],
        )

