"""One-reviewer workflow for paper admission, Claim review, and card publication."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core.store import ReviewStore
from ..literature.ai_extraction import OBSERVATIONAL_DESIGNS

StudyDesign = Literal[
    "randomized_controlled_trial",
    "systematic_review_meta_analysis",
    "cohort_study",
    "case_control_study",
    "cross_sectional_study",
    "case_series",
    "case_report",
    "guideline",
    "other",
    "uncertain",
]


class ClaimReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    corrected_text: str | None = Field(default=None, max_length=2000)
    corrected_study_design: StudyDesign | None = None
    inference: Literal["causal", "associational", "descriptive"] | None = None
    grade: Literal["high", "moderate", "low", "very_low"] | None = None
    condition_code: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> ClaimReviewInput:
        required = (
            self.corrected_text,
            self.corrected_study_design,
            self.inference,
            self.grade,
            self.condition_code,
        )
        if self.decision == "approved" and any(not value for value in required):
            raise ValueError("approved claims require corrected evidence fields")
        if (
            self.decision == "approved"
            and self.corrected_study_design in OBSERVATIONAL_DESIGNS
            and self.inference == "causal"
        ):
            raise ValueError("observational claims cannot be approved as causal")
        return self


class EvidenceReviewService:
    def __init__(self, store: ReviewStore) -> None:
        self.store = store

    def admit_paper(
        self, paper_id: str, *, reviewer: str, condition_codes: list[str]
    ) -> None:
        self.store.admit_paper(
            paper_id,
            reviewer=_reviewer(reviewer),
            condition_codes=tuple(
                dict.fromkeys(code.strip() for code in condition_codes if code.strip())
            ),
        )

    def reject_paper(self, paper_id: str, *, reviewer: str) -> None:
        self.store.reject_paper(paper_id, reviewer=_reviewer(reviewer))

    def review_claim(
        self, claim_id: str, *, reviewer: str, review: ClaimReviewInput
    ) -> None:
        values = review.model_dump()
        if review.decision == "rejected":
            values.update(
                corrected_text=None,
                corrected_study_design=None,
                inference=None,
                grade=None,
                condition_code=None,
            )
        self.store.review_claim(claim_id, reviewer=_reviewer(reviewer), **values)

    def create_card_draft(
        self,
        *,
        condition_code: str,
        version: str,
        claim_ids: list[str],
        reviewer: str,
        patient_body: str,
    ) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("knowledge card version must use semantic versioning")
        return self.store.create_card(
            condition_code=condition_code,
            version=version,
            claim_ids=tuple(dict.fromkeys(claim_ids)),
            reviewer=_reviewer(reviewer),
            patient_body=patient_body,
        )

    def transition_card(self, card_id: str, *, reviewer: str, target: str) -> None:
        self.store.transition_card(card_id, reviewer=_reviewer(reviewer), target=target)


def _reviewer(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("reviewer identity is required")
    return normalized
