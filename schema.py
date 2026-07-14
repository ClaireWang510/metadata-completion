"""Pydantic models for candidates on the blackboard and the final completed metadata."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


LinkClass = Literal[
    "official_code",
    "official_dataset",
    "official_project",
    "cited_external",
    "template_boilerplate",
    "other",
]

VenueType = Literal["conference", "journal", "workshop", "preprint", "unknown"]
PublicationStatus = Literal[
    "submitted", "accepted", "published", "preprint", "unknown"
]
Decision = Literal["accepted", "conflicted", "abstained"]


class Candidate(BaseModel):
    """A single candidate value written to the blackboard."""
    field: str                            # e.g. "venue", "authors[0].affiliations", "resource_links[2].link_class"
    value: Any
    source: str                           # tool / agent identifier, e.g. "openalex:W4392..."
    evidence: str = ""                    # short quotation / json path
    confidence: float = 0.0               # 0.0 - 1.0
    identity_score: float = 1.0           # source record is the same paper
    extraction_score: float = 1.0         # fields were parsed unambiguously
    source_reliability: float = 0.5       # field/source calibration prior
    corroboration_group: str = ""         # shared upstream data are one group
    evidence_url: str = ""
    json_path: str = ""
    quote: str = ""
    retrieved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    decision: Decision = "abstained"
    agent: str = ""                       # agent id
    round_id: int = 0


class VenueValue(BaseModel):
    name: str
    type: VenueType = "unknown"
    year: Optional[int] = None
    doi: Optional[str] = None
    publication_status: PublicationStatus = "unknown"
    source: str = ""
    evidence: str = ""
    evidence_url: str = ""
    json_path: str = ""
    retrieved_at: str = ""
    confidence: float = 0.0
    decision: Decision = "abstained"


class InstitutionValue(BaseModel):
    """An affiliation as normalized for evaluation, preserving source wording."""
    name: str
    raw_name: Optional[str] = None


class AffiliationValue(BaseModel):
    author_name: str
    affiliations: list[InstitutionValue] = Field(default_factory=list)
    source: str = ""
    evidence: str = ""
    evidence_url: str = ""
    json_path: str = ""
    retrieved_at: str = ""
    confidence: float = 0.0
    decision: Decision = "abstained"

    @field_validator("affiliations", mode="before")
    @classmethod
    def _upgrade_legacy_strings(cls, value: Any) -> Any:
        return [{"name": item, "raw_name": item} if isinstance(item, str) else item
                for item in (value or [])]


class LinkValue(BaseModel):
    url: str
    link_class: LinkClass = "other"
    rationale: str = ""
    source: str = ""
    evidence: str = ""
    retrieved_at: str = ""
    confidence: float = 0.0
    decision: Decision = "abstained"


class CompletedMetadata(BaseModel):
    arxiv_id: str
    # The stable arXiv DOI is independent of the publisher/venue DOI below.
    arxiv_doi: Optional[str] = None
    venue_completed: Optional[VenueValue] = None
    affiliations_completed: list[AffiliationValue] = Field(default_factory=list)
    resource_links_completed: list[LinkValue] = Field(default_factory=list)
    iterations: int = 0
    stopped_reason: str = ""
