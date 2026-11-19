"""Read-only transcript parser with exact starts and estimated cue boundaries."""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}")
CAPTION = re.compile(r"^\s*-\s+`([^`]+)`\s+(.*)$")


def seconds(value: str) -> float:
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def verify_ref(ref: dict) -> tuple[Path, bytes]:
    relative = Path(ref["relative_path"])
    source = (ROOT / relative).resolve()
    if not source.is_relative_to(ROOT.resolve()):
        raise ValueError(f"Source path escapes project: {relative}")
    if not source.is_file():
        raise FileNotFoundError(f"Missing source: {relative}")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ref.get("sha256"):
        raise ValueError(f"SHA-256 mismatch for {relative}: {digest}")
    return source, raw


def parse_markdown(ref: dict, raw: bytes) -> dict:
    bom = raw.startswith(bytes.fromhex("efbbbf"))
    text = raw.decode("utf-8-sig" if bom else "utf-8")
    lines = text.splitlines()
    title = lines[0].removeprefix("#").strip() if lines else ""
    section = next((i for i, line in enumerate(lines)
                    if line.strip().lower().startswith("## transcript")), None)
    if section is None:
        raise ValueError(f"Transcript section missing: {ref['relative_path']}")
    url_line = next((x for x in lines if x.startswith("- **Video:**")), "")
    url = url_line.split("**:", 1)[-1].strip() if url_line else None
    duration_line = next((x for x in lines if x.startswith("- **Duration:**")), "")
    duration_match = re.search(r"(\d+)\s+seconds", duration_line)
    duration = int(duration_match.group(1)) if duration_match else None
    segments, warnings = [], []
    for line_number, line in enumerate(lines[section + 1:], section + 2):
        if not line.strip():
            continue
        match = CAPTION.match(line)
        if not match:
            warnings.append(f"Line {line_number}: caption row has no standard timestamp wrapper.")
            segments.append({"order": len(segments) + 1, "source_line_number": line_number,
                             "timestamp": None, "start_seconds": None, "text": line.strip(),
                             "timestamp_status": "missing_or_malformed"})
            continue
        timestamp, caption = match.groups()
        valid = bool(STAMP.fullmatch(timestamp))
        if not valid:
            warnings.append(f"Line {line_number}: malformed timestamp {timestamp!r}.")
        segments.append({"order": len(segments) + 1, "source_line_number": line_number,
                         "timestamp": timestamp, "start_seconds": seconds(timestamp) if valid else None,
                         "text": caption, "timestamp_status": "exact_source_start" if valid else "malformed"})
    if not segments:
        raise ValueError(f"No transcript caption rows: {ref['relative_path']}")
    times = [s["start_seconds"] for s in segments if s["start_seconds"] is not None]
    if len(times) != len(segments):
        warnings.append("One or more caption rows lack a parseable source timestamp.")
    if any(b <= a for a, b in zip(times, times[1:])):
        warnings.append("Source caption timestamps are not strictly increasing.")
    for i, segment in enumerate(segments):
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        segment["end_timestamp_estimated"] = nxt["timestamp"] if nxt else None
        segment["estimated_duration_seconds"] = (
            round(nxt["start_seconds"] - segment["start_seconds"], 3)
            if nxt and nxt["start_seconds"] is not None and segment["start_seconds"] is not None else None)
        segment["end_boundary_basis"] = "next_caption_start_estimate" if nxt else "unavailable_for_final_caption"
    return {"title": title, "source_url": url, "duration_seconds": duration,
            "encoding": "UTF-8 with BOM" if bom else "UTF-8", "segments": segments,
            "warnings": warnings, "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "transcript_text": text}
