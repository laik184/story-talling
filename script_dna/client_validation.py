"""Versioned client DNA contract, citation, and timeline validator."""
from __future__ import annotations

from evidence_validation import validate_evidence

ARRAY_FIELDS = ("subtopics", "hook_mechanisms", "humor_and_language", "narrative_beats",
                "logical_connections", "dna_elements", "ending_payoffs", "pacing_observations",
                "reveal_callbacks", "additional_techniques", "transcription_warnings", "timeline")
ROOT_FIELDS = {"schema_version", "script_id", "source_sha256", "main_topic", "central_question",
               "subtopics", "research_structure", "hook_mechanisms", "humor_and_language",
               "narrative_beats", "logical_connections", "dna_elements", "ending_payoffs",
               "reveal_callbacks", "pacing_observations", "additional_techniques", "timeline", "transcription_warnings"}


def claim_errors(value, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path}: expected a claim object.")
        return
    expected = {"text", "caption_ids", "excerpt", "confidence"}
    for missing in sorted(expected - value.keys()): errors.append(f"{path}: missing {missing}.")
    for extra in sorted(value.keys() - expected): errors.append(f"{path}: unexpected field {extra}.")
    if not isinstance(value.get("text"), str) or not value["text"].strip(): errors.append(f"{path}.text: must be nonempty text.")
    confidence = value.get("confidence")
    if not isinstance(confidence, str) or confidence not in {"low", "medium", "high"}: errors.append(f"{path}.confidence: use low, medium, or high.")


def boundary_errors(value, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path}: expected a boundary object.")
        return
    if set(value) != {"start_caption_id", "end_caption_id", "status"}:
        errors.append(f"{path}: expected start_caption_id, end_caption_id, and status only.")
    for key in ("start_caption_id", "end_caption_id"):
        if not isinstance(value.get(key), str): errors.append(f"{path}.{key}: must be a caption ID string.")
    if value.get("status") != "inferred_from_caption_span": errors.append(f"{path}.status: must mark boundary as inferred.")


def validate_candidate(dna: dict, source: dict) -> list[str]:
    errors = []
    if not isinstance(dna, dict): return ["$: expected a JSON object."]
    for key in sorted(ROOT_FIELDS - dna.keys()): errors.append(f"$: missing required field {key}.")
    for key in sorted(dna.keys() - ROOT_FIELDS): errors.append(f"$: unexpected top-level field {key}.")
    if dna.get("schema_version") != "2.2.0": errors.append("$.schema_version: expected 2.2.0.")
    if dna.get("script_id") != source["script_id"]: errors.append("$.script_id: does not match requested source.")
    if str(dna.get("source_sha256", "")).casefold() != source["source_sha256"].casefold(): errors.append("$.source_sha256: does not match source transcript.")
    claim_errors(dna.get("main_topic"), "$.main_topic", errors)
    if dna.get("central_question") is not None: claim_errors(dna["central_question"], "$.central_question", errors)
    for key in ARRAY_FIELDS:
        if not isinstance(dna.get(key), list): errors.append(f"$.{key}: expected an array; empty arrays are allowed.")
    claim_lists = ("hook_mechanisms", "humor_and_language", "reveal_callbacks", "ending_payoffs", "pacing_observations", "additional_techniques", "transcription_warnings")
    for key in claim_lists:
        rows = dna.get(key, [])
        if isinstance(rows, list):
            for i, row in enumerate(rows): claim_errors(row, f"$.{key}[{i}]", errors)
    subtopics = dna.get("subtopics", [])
    if isinstance(subtopics, list):
        for i, sub in enumerate(subtopics):
            path = f"$.subtopics[{i}]"
            if not isinstance(sub, dict): errors.append(f"{path}: expected an object."); continue
            if set(sub) != {"topic", "explanation_points"}: errors.append(f"{path}: unexpected or missing fields.")
            claim_errors(sub.get("topic"), path + ".topic", errors)
            points = sub.get("explanation_points")
            if not isinstance(points, list): errors.append(f"{path}.explanation_points: expected an array.")
            else:
                for j, point in enumerate(points): claim_errors(point, f"{path}.explanation_points[{j}]", errors)
    structure = dna.get("research_structure")
    if not isinstance(structure, dict): errors.append("$.research_structure: expected an object.")
    else:
        keys = {"horizontal_coverage", "vertical_depth", "causal_relationships"}
        if set(structure) != keys: errors.append("$.research_structure: has missing or unexpected fields.")
        for key in keys:
            rows = structure.get(key)
            if not isinstance(rows, list): errors.append(f"$.research_structure.{key}: expected an array.")
            else:
                for i, row in enumerate(rows): claim_errors(row, f"$.research_structure.{key}[{i}]", errors)
    captions = source["captions"]
    errors.extend(validate_evidence(dna, captions))
    boundaries = {}
    for key, id_key in (("narrative_beats", "beat_id"), ("dna_elements", "element_id")):
        rows = dna.get(key, [])
        seen = set()
        if not isinstance(rows, list): continue
        for i, row in enumerate(rows):
            path = f"$.{key}[{i}]"
            if not isinstance(row, dict): errors.append(f"{path}: expected an object."); continue
            identity = row.get(id_key)
            if not isinstance(identity, str) or not identity or identity in seen: errors.append(f"{path}.{id_key}: must be unique and nonempty.")
            if isinstance(identity, str): seen.add(identity)
            common = {id_key, "boundary"}
            if key == "narrative_beats":
                if set(row) != common | {"function", "transition_to_next"}: errors.append(f"{path}: missing or unexpected fields.")
                claim_errors(row.get("function"), path + ".function", errors)
                if row.get("transition_to_next") is not None: claim_errors(row["transition_to_next"], path + ".transition_to_next", errors)
            else:
                if set(row) != common | {"name", "category", "claim", "identification_reason"}: errors.append(f"{path}: missing or unexpected fields.")
                for required in ("name", "category"):
                    if not isinstance(row.get(required), str) or not row[required].strip(): errors.append(f"{path}.{required}: must be nonempty text.")
                claim_errors(row.get("claim"), path + ".claim", errors)
                claim_errors(row.get("identification_reason"), path + ".identification_reason", errors)
            boundary = row.get("boundary")
            boundary_errors(boundary, path + ".boundary", errors)
            kind = "narrative_beat" if key == "narrative_beats" else "dna_element"
            if isinstance(identity, str) and isinstance(boundary, dict): boundaries[(kind, identity)] = boundary
    beat_ids = {key[1] for key in boundaries if key[0] == "narrative_beat"}
    links = dna.get("logical_connections", [])
    if isinstance(links, list):
        for i, link in enumerate(links):
            path = f"$.logical_connections[{i}]"
            if not isinstance(link, dict): errors.append(f"{path}: expected an object."); continue
            if set(link) != {"from_beat_id", "to_beat_id", "claim"}: errors.append(f"{path}: missing or unexpected fields.")
            from_id, to_id = link.get("from_beat_id"), link.get("to_beat_id")
            if not isinstance(from_id, str) or not isinstance(to_id, str) or from_id not in beat_ids or to_id not in beat_ids: errors.append(f"{path}: references an unknown beat ID.")
            claim_errors(link.get("claim"), path + ".claim", errors)
    expected_timeline = [{"target_type": kind, "target_id": identity,
        "start_caption_id": boundary.get("start_caption_id"), "end_caption_id": boundary.get("end_caption_id"),
        "boundary_status": boundary.get("status")} for (kind, identity), boundary in boundaries.items()]
    actual_timeline = dna.get("timeline")
    if isinstance(actual_timeline, list):
        expected_by_key = {(row["target_type"], row["target_id"]): row for row in expected_timeline}
        actual_by_key = {}
        for i, row in enumerate(actual_timeline):
            if not isinstance(row, dict): errors.append(f"$.timeline[{i}]: expected an object."); continue
            if set(row) != {"target_type", "target_id", "start_caption_id", "end_caption_id", "boundary_status"}: errors.append(f"$.timeline[{i}]: missing or unexpected fields.")
            target_type, target_id = row.get("target_type"), row.get("target_id")
            if not isinstance(target_type, str) or not isinstance(target_id, str): errors.append(f"$.timeline[{i}]: target type and ID must be strings."); continue
            key = (target_type, target_id)
            if key in actual_by_key: errors.append(f"$.timeline[{i}]: duplicate target.")
            actual_by_key[key] = row
        if set(actual_by_key) != set(expected_by_key): errors.append("$.timeline: must cover every beat and DNA element exactly once.")
        for key, row in expected_by_key.items():
            if key in actual_by_key and actual_by_key[key] != row: errors.append(f"$.timeline: boundary mismatch for {key[0]} {key[1]}.")
    order = {row["caption_id"]: i for i, row in enumerate(captions)}
    for (kind, identity), boundary in boundaries.items():
        start, end = boundary.get("start_caption_id"), boundary.get("end_caption_id")
        if not isinstance(start, str) or not isinstance(end, str) or start not in order or end not in order: errors.append(f"$.{kind}.{identity}: boundary references an unknown caption ID.")
        elif order[start] > order[end]: errors.append(f"$.{kind}.{identity}: start boundary follows end boundary.")
    return list(dict.fromkeys(errors))
