"""Cross-line contracts enforced before patient-visible publication."""

from __future__ import annotations

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
