from __future__ import annotations
from .service_shared import *

class RevisionOperations:
    def improve_script(self, topic: str, script: str, target_duration_seconds: int,
                       evidence: EvidencePackInput | None = None,
                       claim_map: list[ClaimEvidenceInput] | None = None) -> ImproveScriptResult:
        """Return a private, deterministic revision brief; ChatGPT writes the revision."""
        evaluation = self.evaluate_script(topic, script, target_duration_seconds, evidence, claim_map)
        actions: list[ImprovementAction] = []
        if evaluation.duration_check == "shorter_than_target_estimate":
            actions.append(ImprovementAction(
                action_id="duration_expand",
                priority="medium",
                instruction="Add only useful explanation supported by supplied evidence until the spoken draft approaches the target duration.",
                evidence_fact_ids=list(evaluation.unreferenced_fact_ids),
            ))
        elif evaluation.duration_check == "longer_than_target_estimate":
            actions.append(ImprovementAction(
                action_id="duration_trim",
                priority="medium",
                instruction="Shorten repetition and secondary explanation while preserving the evidence needed for the answer.",
                evidence_fact_ids=[fact_id for row in evaluation.claim_bindings for fact_id in row.supplied_fact_ids],
            ))
        if evaluation.originality and evaluation.originality.lexical_status in {
            "reject_verbatim_overlap", "needs_human_originality_review"
        }:
            actions.append(ImprovementAction(
                action_id="originality_rewrite",
                priority="high",
                instruction="Rewrite any potentially overlapping wording in fresh language; inspect the flagged passages locally and have a human review semantic similarity.",
            ))
        if evaluation.unreferenced_fact_ids:
            actions.append(ImprovementAction(
                action_id="evidence_coverage",
                priority="medium",
                instruction="Decide whether these supplied facts are relevant; if used, add a claim-to-fact ID binding and retain source qualifications.",
                evidence_fact_ids=evaluation.unreferenced_fact_ids,
            ))
        unknown_bindings = [
            fact_id for row in evaluation.claim_bindings for fact_id in row.unknown_fact_ids
        ]
        if unknown_bindings:
            actions.append(ImprovementAction(
                action_id="repair_evidence_ids",
                priority="high",
                instruction="Replace unknown evidence IDs with IDs from the supplied evidence pack or remove the unsupported mapping.",
            ))
        low_confidence = [fact.fact_id for fact in evidence.facts if fact.confidence == "low"] if evidence else []
        if low_confidence:
            actions.append(ImprovementAction(
                action_id="qualify_low_confidence",
                priority="high",
                instruction="State the uncertainty close to each low-confidence claim and avoid presenting it as established fact.",
                evidence_fact_ids=low_confidence,
            ))
        if not actions:
            actions.append(ImprovementAction(
                action_id="editorial_review",
                priority="low",
                instruction="Apply the supplied rubric in ChatGPT, check every factual clause against its source, and keep human semantic originality review pending.",
            ))
        return ImproveScriptResult(
            evaluation=evaluation,
            action_items=actions,
            preserve=[
                "Keep the script tied to supplied source-linked facts.",
                "Keep material uncertainty and source limitations next to the affected claim.",
                "Do not copy wording from the private third-party transcript corpus.",
            ],
            rewrite_performed_by_tool=False,
            limitations=[
                "This deterministic tool returns editorial actions only; ChatGPT or a human performs any rewrite.",
                "No model call or semantic quality score is generated.",
            ],
        )

    def health(self) -> dict[str, Any]:
        if not self.db_path.is_file() or not self.corpus_dir.joinpath("videos").is_dir():
            return {"status": "not_ready"}
        if not self.retriever.pattern_path.is_file() or not self.retriever.rules_path.is_file() or not RUBRIC_PATH.is_file():
            return {"status": "not_ready"}
        try:
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=2) as connection:
                record = connection.execute(
                    "SELECT COUNT(*) FROM consolidated_story_patterns WHERE pattern_version=?",
                    (PHASE4_VERSION,),
                ).fetchone()
            if not record or int(record[0]) < 1:
                return {"status": "not_ready"}
        except sqlite3.Error as exc:
            LOGGER.warning("health check failed (%s)", type(exc).__name__)
            return {"status": "not_ready"}
        return {"status": "ok", "pattern_database_ready": True, "originality_corpus_ready": True}

