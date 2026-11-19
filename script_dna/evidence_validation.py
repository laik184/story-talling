"""Structural evidence and inferred-boundary checks for semantic results."""
from __future__ import annotations

import re


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def claim_nodes(value, path="$", findings=None):
    findings = [] if findings is None else findings
    if isinstance(value, dict):
        is_legacy_claim = "claim" in value and "supporting_excerpt" in value
        if any(key in value for key in ("source_caption_ids", "text", "caption_ids")) or is_legacy_claim:
            findings.append((path, value))
        for key, child in value.items():
            claim_nodes(child, f"{path}.{key}", findings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            claim_nodes(child, f"{path}[{index}]", findings)
    return findings


def validate_evidence(analysis: dict, captions: list[dict]) -> list[str]:
    errors = []
    by_id = {item["caption_id"]: item for item in captions}
    nodes = claim_nodes(analysis)
    if not nodes:
        errors.append("No semantic claim nodes were returned.")
    for path, node in nodes:
        ids = node.get("source_caption_ids", node.get("caption_ids"))
        excerpt = node.get("supporting_excerpt", node.get("excerpt"))
        if not isinstance(ids, list) or not ids:
            errors.append(f"{path}: semantic assertion has no source caption IDs.")
            continue
        unknown = [caption_id for caption_id in ids
                   if not isinstance(caption_id, str) or caption_id not in by_id]
        if unknown:
            errors.append(f"{path}: unknown source caption IDs: {unknown}.")
            continue
        if len(set(ids)) != len(ids):
            errors.append(f"{path}: duplicate source caption IDs.")
        if not isinstance(excerpt, str) or not excerpt.strip():
            errors.append(f"{path}: semantic assertion has no supporting excerpt.")
            continue
        source = normalize(" ".join(by_id[caption_id]["text"] for caption_id in ids))
        if normalize(excerpt) not in source:
            errors.append(f"{path}: excerpt does not occur in cited captions.")
    return errors


def build_timeline(analysis: dict, captions: list[dict]) -> dict:
    by_id = {item["caption_id"]: item for item in captions}
    raw_beats = analysis.get("narrative_beats", [])
    raw_elements = analysis.get("dna_elements", [])
    beats = raw_beats if isinstance(raw_beats, list) else []
    elements = raw_elements if isinstance(raw_elements, list) else []
    errors = []
    beat_ids = {beat.get("beat_id") for beat in beats if isinstance(beat, dict)}
    for field, items, identity in (("narrative_beats", beats, "beat_id"),
                                   ("dna_elements", elements, "element_id")):
        for item in items:
            if not isinstance(item, dict):
                errors.append(f"{field}: interval item must be an object.")
                continue
            start_id, end_id = item.get("boundary_start_caption_id"), item.get("boundary_end_caption_id")
            if not isinstance(start_id, str) or not isinstance(end_id, str) or start_id not in by_id or end_id not in by_id:
                errors.append(f"{field}/{item.get(identity)}: boundary caption ID is missing or unknown.")
                continue
            if by_id[start_id]["order"] > by_id[end_id]["order"]:
                errors.append(f"{field}/{item.get(identity)}: boundary starts after it ends.")
    for link in analysis.get("logical_connections", []):
        if (not isinstance(link, dict) or not isinstance(link.get("from_beat_id"), str)
                or not isinstance(link.get("to_beat_id"), str)
                or link.get("from_beat_id") not in beat_ids or link.get("to_beat_id") not in beat_ids):
            errors.append("logical_connections: references an unknown narrative beat.")
    def intervals(items, identity):
        output = []
        for item in items:
            if not isinstance(item, dict):
                continue
            start_id = item.get("boundary_start_caption_id")
            end_id = item.get("boundary_end_caption_id")
            if start_id in by_id and end_id in by_id:
                end_start = by_id[end_id]["source_start_timestamp"]
                output.append({"id": item.get(identity),
                               "start_caption_id": start_id,
                               "start_caption_start_timestamp": by_id[start_id]["source_start_timestamp"],
                               "start_timestamp": by_id[start_id]["source_start_timestamp"],
                               "end_caption_id": end_id,
                               "end_caption_start_timestamp": end_start,
                               "end_timestamp": end_start,
                               "end_timestamp_compatibility_note": "Legacy alias of end_caption_start_timestamp; not an exact semantic end.",
                               "boundary_status": item.get("boundary_status", "inferred_from_caption_span"),
                               "start_timestamp_precision": "source caption start exact; semantic boundary inferred",
                               "end_timestamp_precision": "source caption start exact; semantic boundary inferred"})
        return output
    return {"errors": errors,
            "source_caption_starts": [{"caption_id": c["caption_id"],
                                       "timestamp": c["source_start_timestamp"],
                                       "precision": c["timestamp_status"]} for c in captions],
            "narrative_beat_intervals": intervals(beats, "beat_id"),
            "dna_element_intervals": intervals(elements, "element_id")}
