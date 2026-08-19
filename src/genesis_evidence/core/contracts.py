"""Cross-line contracts enforced before patient-visible publication."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CardStatus = Literal["draft", "in_review", "approved", "published", "rejected", "stale"]
ReportStatus = Literal[
    "uploaded",
    "extracted",
    "pending_confirmation",
    "confirmed",
    "assessed",
    "abandoned",
]


class ClaimReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    locator: str = Field(min_length=1)


class GradeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["high", "moderate", "low", "very_low"]
    reviewer: str = Field(min_length=1)
    rated_at: datetime


class KnowledgeCardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_code: str = Field(pattern=r"^COND_[A-Z0-9_]+$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    status: CardStatus
    claims: list[ClaimReference] = Field(min_length=1)
    grade: GradeRecord
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime
    published_at: datetime | None = None
    patient_visible_body: str = ""

    @model_validator(mode="after")
    def require_publication_fields(self) -> KnowledgeCardPayload:
        if self.status == "published" and (
            self.published_at is None or not self.patient_visible_body.strip()
        ):
            raise ValueError("published cards require published_at and patient_visible_body")
        return self


class PatientFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_code: str
    card_id: str = Field(min_length=1)
    card_version: str = Field(min_length=1)
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    needs_recheck: bool
    department: str
    epidemiology_background: str = ""


class EvidenceMatchObservation(BaseModel):
    """One user-confirmed Health-Flow observation crossing the service boundary."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1, max_length=160)
    confirmation_status: Literal["confirmed"]
    metric_code: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=32)
    reference_low: float | None = None
    reference_high: float | None = None
    evidence_text: str = Field(min_length=1, max_length=600)
    source_file_index: int = Field(ge=1)
    source_page: int = Field(ge=1)
    source_id: str | None = Field(default=None, max_length=240)
    bbox_normalized: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_numbers(self) -> EvidenceMatchObservation:
        numbers = (self.value, self.reference_low, self.reference_high)
        if any(number is not None and not math.isfinite(number) for number in numbers):
            raise ValueError("observation values must be finite")
        if (
            self.reference_low is not None
            and self.reference_high is not None
            and self.reference_low > self.reference_high
        ):
            raise ValueError("reference_low cannot exceed reference_high")
        if self.bbox_normalized is not None:
            if any(
                not math.isfinite(coordinate) or not 0 <= coordinate <= 1000
                for coordinate in self.bbox_normalized
            ):
                raise ValueError("bbox_normalized coordinates must be finite values in 0..1000")
            if (
                self.bbox_normalized[0] > self.bbox_normalized[2]
                or self.bbox_normalized[1] > self.bbox_normalized[3]
            ):
                raise ValueError("bbox_normalized must be ordered as x1,y1,x2,y2")
        return self


class EvidenceMatchRequest(BaseModel):
    """Versioned request for deterministic published-card matching."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2"]
    observations: list[EvidenceMatchObservation] = Field(max_length=600)

    @model_validator(mode="after")
    def unique_observation_ids(self) -> EvidenceMatchRequest:
        ids = [item.observation_id for item in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("observation_id must be unique")
        return self


class EvidenceSourceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    metric_code: str
    value: float
    unit: str
    reference_low: float | None = None
    reference_high: float | None = None
    evidence_text: str
    source_file_index: int = Field(ge=1)
    source_page: int = Field(ge=1)
    source_id: str | None = None
    bbox_normalized: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_bbox(self) -> EvidenceSourceObservation:
        if self.bbox_normalized is not None:
            if any(
                not math.isfinite(coordinate) or not 0 <= coordinate <= 1000
                for coordinate in self.bbox_normalized
            ):
                raise ValueError("bbox_normalized coordinates must be finite values in 0..1000")
            if (
                self.bbox_normalized[0] > self.bbox_normalized[2]
                or self.bbox_normalized[1] > self.bbox_normalized[3]
            ):
                raise ValueError("bbox_normalized must be ordered as x1,y1,x2,y2")
        return self


class EvidenceSourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    paper_id: str
    paper_title: str
    doi: str | None = None
    evidence: str
    locator: str


class PublishedEvidenceCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    condition_code: str
    scope_key: str = Field(min_length=1)
    version: str
    status: Literal["published"]
    grade: Literal["high", "moderate", "low", "very_low"]
    published_at: datetime
    evidence_profile_id: str
    patient_visible_body: str
    sources: list[EvidenceSourceReference] = Field(min_length=1)


class EvidenceSorting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    needs_recheck: bool
    department: str
    epidemiology_background: str


class EvidenceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_code: str
    condition_name: str
    card: PublishedEvidenceCard
    source_observation_ids: list[str]
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    needs_recheck: bool
    department: str
    recheck_direction: str
    epidemiology_background: str
    source_observations: list[EvidenceSourceObservation]
    sorting: EvidenceSorting


class EvidenceUnmatched(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    metric_code: str
    metric_label: str
    condition_codes: list[str]
    reason: Literal["no_published_knowledge_card"]


class EvidenceSkipped(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    reason: Literal[
        "missing_reference_range",
        "within_reference_range",
        "missing_source_evidence",
        "missing_source_page",
        "missing_unit",
        "invalid_value",
        "unknown_metric_code",
    ]


class PatientReplyFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_code: str
    condition_name: str
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    needs_recheck: bool
    department: str
    recheck_direction: str
    card_id: str
    card_version: str
    evidence_profile_id: str
    patient_visible_body: str
    sources: list[EvidenceSourceReference] = Field(min_length=1)
    source_observation_ids: list[str]
    source_observations: list[EvidenceSourceObservation]


class PatientReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Literal["体检报告解读与健康风险提示"]
    summary: str
    findings: list[PatientReplyFinding]
    unmatched_count: int = Field(ge=0)
    disclaimer: str


class EvidenceMatchResponse(BaseModel):
    """Versioned, published-only response consumed by Health-Flow."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2"]
    sorting_version: Literal["published-card-reference-range-v1"]
    correlation_id: str
    findings: list[EvidenceFinding]
    unmatched: list[EvidenceUnmatched]
    skipped: list[EvidenceSkipped]
    message: str
    patient_reply: PatientReply
