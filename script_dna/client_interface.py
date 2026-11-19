"""Local data/validation operations; semantic analysis belongs to the MCP client."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from client_validation import validate_candidate
from parser import parse_markdown, verify_ref

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(__file__).resolve().parent
MANIFEST = ROOT / "prototype_manifest.json"
SCHEMA = BASE / "dynamic_script_dna.schema.json"
INSTRUCTIONS = BASE / "DYNAMIC_DNA_INSTRUCTIONS.md"
PROFILE_DIR = BASE / "client_profiles"


class FactoHolicClientInterface:
    """Deterministic local operations exposed for a future MCP wrapper."""

    def __init__(self, profile_dir: Path | None = None):
        self.profile_dir = profile_dir or PROFILE_DIR
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
        self.refs = {row["script_id"]: row for row in self.manifest["references"]}

    def get_selected_transcript(self, script_id: str) -> dict:
        if script_id not in self.refs:
            raise KeyError(f"Unknown selected Script ID: {script_id}")
        ref = self.refs[script_id]
        _, raw = verify_ref(ref)
        parsed = parse_markdown(ref, raw)
        captions = [{"caption_id": f"C{row['order']:04d}", "order": row["order"],
                     "timestamp": row["timestamp"], "timestamp_status": row["timestamp_status"],
                     "text": row["text"]} for row in parsed["segments"]]
        return {"script_id": script_id, "relative_path": ref["relative_path"],
                "title": parsed["title"], "duration_seconds": parsed["duration_seconds"],
                "source_sha256": parsed["raw_sha256"], "parser_warnings": parsed["warnings"],
                "captions": captions}

    def get_analysis_instructions(self) -> str:
        return INSTRUCTIONS.read_text(encoding="utf-8-sig")

    def get_contract_schema(self) -> dict:
        return json.loads(SCHEMA.read_text(encoding="utf-8-sig"))

    def validate_dna_json(self, script_id: str, raw_json: str | dict) -> dict:
        try:
            dna = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        except json.JSONDecodeError as exc:
            return {"valid": False,
                    "errors": [f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}."]}
        try:
            source = self.get_selected_transcript(script_id)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            return {"valid": False, "errors": [str(exc)]}
        errors = validate_candidate(dna, source)
        return {"valid": not errors, "errors": errors,
                "semantic_interpretations_independently_verified": False}

    def _make_profile(self, script_id: str, dna: dict) -> dict:
        source = self.get_selected_transcript(script_id)
        by_id = {row["caption_id"]: row for row in source["captions"]}
        timeline = {"source_caption_starts": [
            {"caption_id": row["caption_id"], "timestamp": row["timestamp"],
             "precision": row["timestamp_status"]} for row in source["captions"]],
            "narrative_beat_boundaries": [], "dna_element_boundaries": []}
        for beat in dna["narrative_beats"]:
            timeline["narrative_beat_boundaries"].append(
                self._boundary_row(beat["beat_id"], beat["boundary"], by_id))
        for element in dna["dna_elements"]:
            timeline["dna_element_boundaries"].append(
                self._boundary_row(element["element_id"], element["boundary"], by_id))
        warnings = source["parser_warnings"] + [row["text"] for row in dna["transcription_warnings"]]
        return {"schema_version": "2.2.0", "script_id": script_id,
                "source_metadata": {"relative_path": source["relative_path"],
                    "title": source["title"], "duration_seconds": source["duration_seconds"],
                    "sha256": source["source_sha256"]},
                "dna": dna, "timeline": timeline, "transcription_warnings": warnings,
                "validation": {"status": "STRUCTURAL_VALIDATION_PASS",
                    "semantic_interpretations_independently_verified": False}}

    @staticmethod
    def _boundary_row(identity: str, boundary: dict, captions: dict) -> dict:
        start = captions[boundary["start_caption_id"]]
        end = captions[boundary["end_caption_id"]]
        return {"id": identity, "start_caption_id": start["caption_id"],
                "start_timestamp": start["timestamp"], "end_caption_id": end["caption_id"],
                "end_caption_start_timestamp": end["timestamp"],
                "boundary_status": "inferred_from_caption_span",
                "timestamp_precision": "caption starts are source-exact; semantic boundary is inferred"}

    def save_validated_profile(self, script_id: str, raw_json: str | dict) -> dict:
        result = self.validate_dna_json(script_id, raw_json)
        if not result["valid"]: return {"saved": False, **result}
        dna = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        profile = self._make_profile(script_id, dna)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        target = self.profile_dir / f"{script_id}.json"
        try:
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(profile, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
        except FileExistsError:
            return {"saved": False, "errors": [f"Profile already exists and was not overwritten: {target}"]}
        return {"saved": True, "path": str(target), "validation": profile["validation"]}

    def get_stored_profile(self, script_id: str) -> dict:
        if script_id not in self.refs: raise KeyError(f"Unknown selected Script ID: {script_id}")
        path = self.profile_dir / f"{script_id}.json"
        return json.loads(path.read_text(encoding="utf-8-sig"))


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    actions = cli.add_subparsers(dest="action", required=True)
    for name in ("transcript", "validate", "save", "get-profile"):
        sub = actions.add_parser(name)
        sub.add_argument("script_id")
        if name in ("validate", "save"): sub.add_argument("json_file", type=Path)
    actions.add_parser("instructions")
    actions.add_parser("schema")
    args = cli.parse_args(argv)
    service = FactoHolicClientInterface()
    try:
        if args.action == "instructions": print(service.get_analysis_instructions())
        elif args.action == "schema": print(json.dumps(service.get_contract_schema(), ensure_ascii=False, indent=2))
        elif args.action == "transcript": print(json.dumps(service.get_selected_transcript(args.script_id), ensure_ascii=False, indent=2))
        elif args.action == "get-profile": print(json.dumps(service.get_stored_profile(args.script_id), ensure_ascii=False, indent=2))
        else:
            content = args.json_file.read_text(encoding="utf-8-sig")
            result = (service.validate_dna_json(args.script_id, content) if args.action == "validate"
                      else service.save_validated_profile(args.script_id, content))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("valid", result.get("saved", False)) else 1
        return 0
    except (KeyError, FileNotFoundError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
