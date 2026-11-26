"""Markdown transcript parsing with source line and timestamp provenance."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


TIMESTAMP_RE = re.compile(r"^\s*-\s*`(?P<stamp>\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)`\s*(?P<text>.*?)\s*$")
METADATA_RE = re.compile(r"^\s*-\s*\*\*(?P<key>[^*]+):\*\*\s*(?P<value>.*?)\s*$")
AI_SECTION_RE = re.compile(r"^\s*\[AI-SECTION:\s*(?P<name>[^\]]+)\]\s*$", re.IGNORECASE)
ANALYSIS_BEGIN_RE = re.compile(r"^\s*\[AI-ANALYSIS:\s*BEGIN\s*\]\s*$", re.IGNORECASE)
TRANSCRIPT_HEADER_RE = re.compile(r"^\s*##\s+Transcript\s*$", re.IGNORECASE)


@dataclass(slots=True)
class Caption:
    ordinal: int
    timestamp: str
    time_seconds: float
    text: str
    section: str | None
    source_line: int


@dataclass(slots=True)
class ParsedTranscript:
    title: str | None
    metadata: dict[str, str]
    captions: list[Caption]
    transcript_text: str
    analysis_text: str | None
    creator: str | None
    published: str | None
    duration_seconds: int | None
    views: int | None
    likes: int | None
    comments: int | None
    rank: int | None
    transcript_status: str | None
    language_track: str | None
    warnings: list[str]
    parsing_status: str

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["captions"] = [asdict(caption) for caption in self.captions]
        return data


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _parse_number(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = value.replace(",", "").replace("_", "")
    match = re.search(r"-?\d+", cleaned)
    return int(match.group()) if match else None


def _parse_duration(value: str | None) -> int | None:
    if not value:
        return None
    clock = re.fullmatch(r"\s*(?:(\d+)\s*:\s*)?(\d{1,2})\s*:\s*(\d{1,2})\s*", value)
    if clock:
        hours, minutes, seconds = (int(part or 0) for part in clock.groups())
        return hours * 3600 + minutes * 60 + seconds
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(seconds?|secs?|s)\s*", value, re.IGNORECASE)
    if match:
        return int(float(match.group(1)))
    return None


def _timestamp_seconds(stamp: str) -> float:
    hours, minutes, seconds = stamp.replace(",", ".").split(":")
    minutes_value = int(minutes)
    seconds_value = float(seconds)
    if minutes_value >= 60 or seconds_value >= 60:
        raise ValueError(f"Invalid timestamp component in {stamp!r}.")
    return int(hours) * 3600 + minutes_value * 60 + seconds_value


def parse_markdown(markdown: str) -> ParsedTranscript:
    lines = markdown.splitlines()
    title = None
    metadata: dict[str, str] = {}
    warnings: list[str] = []
    transcript_header_line: int | None = None
    analysis_start_line: int | None = None

    for index, line in enumerate(lines):
        if title is None:
            heading = re.match(r"^\s*#\s+(.+?)\s*#*\s*$", line)
            if heading:
                title = heading.group(1).strip()
        if transcript_header_line is None and TRANSCRIPT_HEADER_RE.match(line):
            transcript_header_line = index
        if ANALYSIS_BEGIN_RE.match(line):
            analysis_start_line = index
            break
        metadata_match = METADATA_RE.match(line)
        if metadata_match:
            metadata[_key(metadata_match.group("key"))] = metadata_match.group("value").strip()

    if transcript_header_line is None:
        warnings.append("missing_transcript_header")

    caption_start = transcript_header_line + 1 if transcript_header_line is not None else 0
    caption_end = analysis_start_line if analysis_start_line is not None else len(lines)
    captions: list[Caption] = []
    active_section: str | None = None
    for index in range(caption_start, caption_end):
        line = lines[index]
        section_match = AI_SECTION_RE.match(line)
        if section_match:
            active_section = section_match.group("name").strip()
            continue
        match = TIMESTAMP_RE.match(line)
        if match:
            text = match.group("text").strip()
            if not text:
                warnings.append(f"empty_caption_line:{index + 1}")
            try:
                time_seconds = _timestamp_seconds(match.group("stamp"))
            except ValueError:
                warnings.append(f"malformed_timestamp_line:{index + 1}")
                continue
            captions.append(Caption(
                ordinal=len(captions), timestamp=match.group("stamp"), time_seconds=time_seconds,
                text=text, section=active_section, source_line=index + 1,
            ))
        elif re.search(r"`\d{2}:\d{2}:\d{2}", line):
            warnings.append(f"malformed_timestamp_line:{index + 1}")

    analysis_text = None
    if analysis_start_line is not None:
        analysis_text = "\n".join(lines[analysis_start_line + 1:]).strip() or None
    transcript_text = "\n".join(caption.text for caption in captions if caption.text)
    if not captions:
        warnings.append("no_timestamped_captions")

    date_value = metadata.get("published")
    published = None
    if date_value:
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", date_value)
        if date_match:
            try:
                published = date.fromisoformat(date_match.group()).isoformat()
            except ValueError:
                warnings.append("invalid_published_date")
        else:
            warnings.append("unparsed_published_date")

    duration_seconds = _parse_duration(metadata.get("duration"))
    if metadata.get("duration") and duration_seconds is None:
        warnings.append("unparsed_duration")
    rank_match = re.match(r"\s*(\d+)", metadata.get("rank", ""))
    rank = int(rank_match.group(1)) if rank_match else None
    status = metadata.get("transcript_status")
    language_track = metadata.get("transcript_language_track") or metadata.get("language_track")
    creator = metadata.get("creator") or metadata.get("channel")
    parsing_status = "ok" if not warnings else "warning"

    return ParsedTranscript(
        title=title,
        metadata=metadata,
        captions=captions,
        transcript_text=transcript_text,
        analysis_text=analysis_text,
        creator=creator,
        published=published,
        duration_seconds=duration_seconds,
        views=_parse_number(metadata.get("views")),
        likes=_parse_number(metadata.get("likes")),
        comments=_parse_number(metadata.get("comments")),
        rank=rank,
        transcript_status=status,
        language_track=language_track,
        warnings=warnings,
        parsing_status=parsing_status,
    )
