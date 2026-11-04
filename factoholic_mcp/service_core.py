from __future__ import annotations
from .service_shared import *

class CoreOperations:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.db_path = self.root / "07_database" / "factoholic.sqlite3"
        self.corpus_dir = self.root / "01_dataset" / "raw_transcripts" / "FactoHolic_Top_1000_Accessible_Transcripts"
        self.retriever = PatternRetriever(self.root, version=PHASE4_VERSION, top_k=6)
        self.originality_scanner = CorpusOriginalityScanner(self.corpus_dir)
        self._scan_slots = threading.BoundedSemaphore(value=2)
        try:
            self.rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
            if len(self.rubric.get("dimensions", [])) != 10:
                raise ValueError("rubric dimension count mismatch")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Phase 6 rubric is unavailable or invalid") from exc

    def _validate_request(self, topic: str, context: TopicContextInput | None) -> TopicRequest:
        if len(topic) > MAX_TOPIC_CHARS:
            raise ServiceError("invalid_input", f"topic exceeds {MAX_TOPIC_CHARS} characters")
        data = context.model_dump() if context else {}
        try:
            return TopicRequest(
                topic=topic,
                audience=data.get("audience", "Hindi-speaking general audience"),
                duration_seconds=data.get("duration_seconds", 45),
                language_preference=data.get("language_preference", "Hinglish"),
                tone=data.get("tone", "curious and clear"),
                humor_suitability=data.get("humor_suitability", "adaptive"),
            )
        except ValidationError as exc:
            raise ServiceError("invalid_input", str(exc)) from exc

    def _retrieve(self, topic: str, evidence: EvidencePackInput | None, context: TopicContextInput | None) -> dict[str, Any]:
        facts = _fact_dicts(evidence)
        profile = {"core_subject": topic, "search_terms": [], "story_opportunities": []}
        try:
            return self.retriever.retrieve(topic, profile, facts, context=_context(context))
        except (sqlite3.Error, OSError, ValueError, KeyError) as exc:
            LOGGER.warning("pattern retrieval failed (%s)", type(exc).__name__)
            raise ServiceError("pattern_store_unavailable", "Story pattern search is temporarily unavailable.") from exc

    @staticmethod
    def _pattern_summaries(retrieved: dict[str, Any]) -> list[PatternSummary]:
        result: list[PatternSummary] = []
        for source_phase, key in (("phase4", "phase4_patterns"), ("phase3", "phase3_patterns")):
            for pattern in retrieved.get(key, []):
                result.append(PatternSummary(
                    source_phase=source_phase,
                    pattern_id=str(pattern.get("pattern_id", "")),
                    version=str(pattern.get("pattern_version", "")),
                    name=str(pattern.get("name", "")),
                    mechanism=str(pattern.get("mechanism", "")),
                    narrative_function=str(pattern.get("narrative_function", "")),
                    topic_tags=list(pattern.get("topic_tags", [])),
                    frequency=pattern.get("frequency"),
                    confidence=pattern.get("evidence_confidence", pattern.get("confidence")),
                    evidence_status=str(pattern.get("status", "provisional")),
                    support_count=pattern.get("supporting_video_count"),
                    counterexample_count=pattern.get("counterexample_video_count"),
                    originality_restrictions=str(pattern.get("originality_restrictions", "Use new wording and supplied evidence.")),
                ))
        return result

    @staticmethod
    def _rule_summaries(retrieved: dict[str, Any]) -> list[DecisionRuleSummary]:
        return [DecisionRuleSummary(
            decision_id=str(rule.get("decision_id", "")),
            mechanism=str(rule.get("mechanism", "")),
            intended_function=str(rule.get("intended_function", "")),
            appropriate_context=str(rule.get("appropriate_context", "")),
            inappropriate_context=str(rule.get("inappropriate_context", "")),
            confidence=float(rule.get("confidence", 0.0)),
            evidence_status=str(rule.get("status", "hypothesis only")),
            originality_restrictions=str(rule.get("originality_restrictions", "Use newly written language and evidence.")),
        ) for rule in retrieved.get("decision_rules", [])]

    @staticmethod
    def _evidence_summary(topic: str, evidence: EvidencePackInput | None) -> EvidenceSummary:
        if evidence is None:
            return EvidenceSummary(status="not_supplied", semantic_verification_performed=False)
        bundle = _evidence_bundle(topic, evidence)
        warnings = freshness_issues(bundle) if bundle is not None else []
        warning_ids = []
        for warning in warnings:
            match = re.search(r"fact ([^ ]+) has no source checked", warning)
            if match:
                warning_ids.append(match.group(1))
        return EvidenceSummary(
            status="user_supplied_unverified",
            fact_ids=[fact.fact_id for fact in evidence.facts],
            source_ids=[source.source_id for source in evidence.sources],
            low_confidence_fact_ids=[fact.fact_id for fact in evidence.facts if fact.confidence == "low"],
            freshness_warning_fact_ids=warning_ids,
            semantic_verification_performed=False,
        )

