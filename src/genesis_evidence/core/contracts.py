"""Cross-line contracts enforced before patient-visible publication."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CardStatus = Literal["draft", "in_review", "approved", "published", "rejected", "stale"]
CardContentLayer = Literal["context_only"]
ActionStatus = Literal["not_available"]
ProductStatus = Literal["not_implemented"]
EvidenceStrength = Literal["high", "moderate", "low", "very_low", "mixed"]
ReportStatus = Literal[
    "uploaded",
    "extracted",
    "pending_confirmation",
    "confirmed",
    "assessed",
    "abandoned",
]


def _validate_bbox(value: list[float] | None, *, upper: float | None, field: str) -> None:
    if value is None:
        return
    if any(
        not math.isfinite(coordinate)
        or coordinate < 0
        or (upper is not None and coordinate > upper)
        for coordinate in value
    ):
        suffix = f" in 0..{upper:g}" if upper is not None else ""
        raise ValueError(f"{field} coordinates must be finite values{suffix}")
    if value[0] > value[2] or value[1] > value[3]:
        raise ValueError(f"{field} must be ordered as x1,y1,x2,y2")


def card_capabilities(grade: str) -> dict[str, str]:
    """Map evidence strength to the capabilities exposed to patients."""

    return {
        "content_layer": "context_only",
        "action_status": "not_available",
        "action_message": (
            "证据确定性已达到行动建议门槛，但当前知识卡尚未包含经审核的具体行动内容。"
            if grade in {"moderate", "high"}
            else "当前证据确定性尚未达到具体行动建议门槛。"
        ),
        "product_status": "not_implemented",
    }


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
    source_url: str | None = Field(default=None, max_length=500)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
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
        _validate_bbox(self.bbox, upper=None, field="bbox")
        _validate_bbox(self.bbox_normalized, upper=1000, field="bbox_normalized")
        return self


class EvidenceMatchRequest(BaseModel):
    """Versioned request for deterministic published-card matching."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2", "3"]
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
    source_url: str | None = None
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    bbox_normalized: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_bbox(self) -> EvidenceSourceObservation:
        _validate_bbox(self.bbox, upper=None, field="bbox")
        _validate_bbox(self.bbox_normalized, upper=1000, field="bbox_normalized")
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
    content_layer: CardContentLayer
    action_status: ActionStatus
    action_message: str = ""
    product_status: ProductStatus


class EvidenceSortingV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    needs_recheck: bool
    department: str
    epidemiology_background: str


class EvidenceFindingV2(BaseModel):
    """Legacy one-card finding retained for pre-v3 Health-Flow clients."""

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
    sorting: EvidenceSortingV2
    content_layer: CardContentLayer
    action_status: ActionStatus
    action_message: str = ""
    product_status: ProductStatus


class EvidenceUnmatchedV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    metric_code: str
    metric_label: str
    condition_codes: list[str]
    reason: Literal["no_published_knowledge_card"]


class PatientReplyFindingV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_code: str
    condition_name: str
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    needs_recheck: bool
    department: str
    recheck_direction: str
    card_id: str = Field(min_length=1)
    card_version: str = Field(min_length=1)
    evidence_profile_id: str = Field(min_length=1)
    patient_visible_body: str
    sources: list[EvidenceSourceReference] = Field(min_length=1)
    source_observation_ids: list[str]
    source_observations: list[EvidenceSourceObservation]
    content_layer: CardContentLayer
    action_status: ActionStatus
    action_message: str = ""
    product_status: ProductStatus


class PatientReplyV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Literal["体检报告解读与健康风险提示"]
    summary: str
    findings: list[PatientReplyFindingV2]
    unmatched_count: int = Field(ge=0)
    disclaimer: str


class EvidenceMatchResponseV2(BaseModel):
    """The pre-condition-grouping response contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2"]
    sorting_version: Literal["published-card-reference-range-v1"]
    correlation_id: str
    findings: list[EvidenceFindingV2]
    unmatched: list[EvidenceUnmatchedV2]
    skipped: list[EvidenceSkipped]
    message: str
    patient_reply: PatientReplyV2


class EvidenceSorting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: EvidenceStrength
    needs_recheck: bool
    department: str
    epidemiology_background: str


class EvidenceItem(BaseModel):
    """One metric-level card and its independent report trace."""

    model_config = ConfigDict(extra="forbid")

    metric_code: str
    metric_label: str
    card: PublishedEvidenceCard
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    source_observation_ids: list[str] = Field(min_length=1)
    source_observations: list[EvidenceSourceObservation] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope(self) -> EvidenceItem:
        expected_scope = f"metric:{self.metric_code}"
        if self.card.scope_key != expected_scope:
            raise ValueError("evidence item card scope does not match metric_code")
        return self


class EvidenceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_code: str
    condition_name: str
    source_observation_ids: list[str]
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: EvidenceStrength
    needs_recheck: bool
    department: str
    recheck_direction: str
    epidemiology_background: str
    source_observations: list[EvidenceSourceObservation]
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    sorting: EvidenceSorting
    content_layer: CardContentLayer
    action_status: ActionStatus
    action_message: str = ""
    product_status: ProductStatus


class EvidenceUnmatched(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    metric_code: str
    metric_label: str
    condition_codes: list[str]
    condition_names: list[str] = Field(default_factory=list)
    reason: Literal["no_published_knowledge_card"]

    @model_validator(mode="after")
    def align_condition_names(self) -> EvidenceUnmatched:
        if self.condition_names and len(self.condition_names) != len(self.condition_codes):
            raise ValueError("condition_names must align with condition_codes")
        return self


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
    evidence_strength: EvidenceStrength
    needs_recheck: bool
    department: str
    recheck_direction: str
    source_observation_ids: list[str]
    source_observations: list[EvidenceSourceObservation]
    content_layer: CardContentLayer
    action_status: ActionStatus
    action_message: str = ""
    product_status: ProductStatus
    evidence_items: list[EvidenceItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_patient_evidence_items(self) -> PatientReplyFinding:
        if not self.evidence_items:
            raise ValueError("patient findings require metric-level evidence_items")
        return self


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

    schema_version: Literal["3"]
    sorting_version: Literal["published-card-reference-range-v1"]
    correlation_id: str
    findings: list[EvidenceFinding]
    unmatched: list[EvidenceUnmatched]
    skipped: list[EvidenceSkipped]
    message: str
    patient_reply: PatientReply

    @model_validator(mode="after")
    def require_metric_evidence(self) -> EvidenceMatchResponse:
        if any(not finding.evidence_items for finding in self.findings):
            raise ValueError("findings require metric-level evidence_items")
        if any(not finding.evidence_items for finding in self.patient_reply.findings):
            raise ValueError("patient findings require evidence_items")
        return self
