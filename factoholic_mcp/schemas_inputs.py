"""Structured MCP inputs and privacy-safe outputs."""
from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceSourceInput(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    publisher: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2_000)
    source_type: Literal["primary", "peer_reviewed", "authoritative_secondary", "secondary"]
    accessed_at: str = Field(min_length=10, max_length=40)
    evidence_excerpt: str = Field(default="", max_length=5_000)
    limitations: str = Field(default="", max_length=2_000)

    @field_validator("url")
    @classmethod
    def require_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("source URL must use HTTPS")
        return value

    @field_validator("accessed_at")
    @classmethod
    def require_iso_date(cls, value: str) -> str:
        from datetime import date

        date.fromisoformat(value[:10])
        return value[:10]


class EvidenceFactInput(StrictModel):
    fact_id: str = Field(min_length=1, max_length=100)
    claim: str = Field(min_length=1, max_length=2_000)
    source_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=1, max_length=10)
    confidence: Literal["high", "medium", "low"] = "medium"
    time_sensitive: bool = False

    @field_validator("source_ids")
    @classmethod
    def source_ids_are_unique(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values) or len(values) != len(set(values)):
            raise ValueError("source_ids must be non-empty and unique")
        return values


class EvidencePackInput(StrictModel):
    sources: list[EvidenceSourceInput] = Field(default_factory=list, max_length=30)
    facts: list[EvidenceFactInput] = Field(default_factory=list, max_length=50)
    open_questions: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def references_are_consistent(self) -> "EvidencePackInput":
        source_ids = [source.source_id for source in self.sources]
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("evidence source IDs must be unique")
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("evidence fact IDs must be unique")
        unknown = {sid for fact in self.facts for sid in fact.source_ids} - set(source_ids)
        if unknown:
            raise ValueError("every fact source ID must exist in the supplied source list")
        if self.facts and not self.sources:
            raise ValueError("facts require at least one source")
        return self


class TopicContextInput(StrictModel):
    audience: str = Field(default="Hindi-speaking general audience", min_length=1, max_length=500)
    duration_seconds: int = Field(default=45, ge=15, le=180)
    language_preference: Literal["Hindi", "Hinglish", "Hindi-English"] = "Hinglish"
    tone: str = Field(default="curious and clear", min_length=1, max_length=300)
    complexity: Literal["low", "medium", "high"]
    sensitivity: Literal["low", "medium", "high"]
    humor_suitability: Literal["none", "low", "moderate", "adaptive"] = "adaptive"
    evidence_confidence: Literal["low", "medium", "high"]
    hook_requirements: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=lambda: [
            "immediate clarity", "evidence-backed curiosity", "spoken naturalness", "promise matched by payoff"
        ], min_length=1, max_length=12,
    )
    footage_reviewed: bool = False
    promotional_content: bool = False


class ClaimEvidenceInput(StrictModel):
    claim_id: str = Field(min_length=1, max_length=100)
    evidence_fact_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)


