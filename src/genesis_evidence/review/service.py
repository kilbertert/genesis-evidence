"""One-reviewer workflow for paper admission, Claim review, and card publication."""

from __future__ import annotations

import re
from datetime import date
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
    "controlled_feeding_metabolic_study",
    "bioavailability_pharmacokinetic_study",
    "biomarker_validation_study",
    "non_randomized_controlled_study",
    "natural_experiment",
    "ecological_study",
    "animal_study",
    "in_vitro_study",
    "case_series",
    "case_report",
    "guideline",
    "other",
    "uncertain",
]


class RiskOfBiasInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Literal[
        "rob2",
        "robins_i",
        "robis",
        "amstar2",
        "diagnostic_accuracy",
        "exposure_study",
        "safety_signal",
        "other",
    ]
    overall: Literal["low", "some_concerns", "high", "critical", "uncertain"]
    rationale: str = Field(min_length=1, max_length=4000)


class EvidenceProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    certainty: Literal["high", "moderate", "low", "very_low"]
    certainty_rationale: str = Field(min_length=1, max_length=5000)
    evidence_cutoff_date: date
    estimate_target: str = Field(min_length=1, max_length=1000)
    evidence_body_complete: bool
    interpretations: dict[
        str, Literal["supports", "does_not_support", "mixed", "uncertain", "not_reported"]
    ]

    @model_validator(mode="after")
    def require_complete_evidence_body(self) -> EvidenceProfileInput:
        if not self.evidence_body_complete:
            raise ValueError("patient evidence profiles require a complete eligible evidence body")
        if not self.interpretations:
            raise ValueError("evidence profile requires result interpretations")
        return self


class ClaimReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    corrected_text: str | None = Field(default=None, max_length=2000)
    corrected_study_design: StudyDesign | None = None
    inference: Literal["causal", "associational", "descriptive"] | None = None
    risk_of_bias: RiskOfBiasInput | None = None
    applicability: str | None = Field(default=None, max_length=4000)
    condition_code: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> ClaimReviewInput:
        required = (
            self.corrected_text,
            self.corrected_study_design,
            self.inference,
            self.risk_of_bias,
            self.applicability,
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
        if self.decision == "approved" and self.risk_of_bias:
            allowed_tools = {
                "randomized_controlled_trial": {"rob2"},
                "systematic_review_meta_analysis": {"robis", "amstar2"},
                "non_randomized_controlled_study": {"robins_i"},
                "natural_experiment": {"robins_i"},
                "biomarker_validation_study": {"diagnostic_accuracy"},
                "bioavailability_pharmacokinetic_study": {"other"},
                "controlled_feeding_metabolic_study": {"rob2", "other"},
                "cohort_study": {"exposure_study"},
                "case_control_study": {"exposure_study"},
                "cross_sectional_study": {"exposure_study"},
                "ecological_study": {"exposure_study"},
                "case_series": {"safety_signal"},
                "case_report": {"safety_signal"},
                "animal_study": {"other"},
                "in_vitro_study": {"other"},
            }.get(self.corrected_study_design)
            if allowed_tools and self.risk_of_bias.tool not in allowed_tools:
                raise ValueError("risk-of-bias tool does not match the reviewed study design")
        return self


class EvidenceReviewService:
    def __init__(self, store: ReviewStore) -> None:
        self.store = store

    def admit_paper(
        self,
        paper_id: str,
        *,
        reviewer: str,
        condition_codes: list[str],
        consistency_resolution: str | None = None,
    ) -> None:
        self.store.admit_paper(
            paper_id,
            reviewer=_reviewer(reviewer),
            condition_codes=tuple(
                dict.fromkeys(code.strip() for code in condition_codes if code.strip())
            ),
            consistency_resolution=(consistency_resolution or "").strip() or None,
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
                risk_of_bias=None,
                applicability=None,
                condition_code=None,
            )
        risk_of_bias = values.pop("risk_of_bias")
        values["risk_of_bias"] = risk_of_bias
        self.store.review_claim(claim_id, reviewer=_reviewer(reviewer), **values)

    def create_card_draft(
        self,
        *,
        condition_code: str,
        version: str,
        claim_ids: list[str],
        reviewer: str,
        patient_body: str,
        profile: EvidenceProfileInput,
    ) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("knowledge card version must use semantic versioning")
        profile = EvidenceProfileInput.model_validate(profile)
        return self.store.create_card(
            condition_code=condition_code,
            version=version,
            claim_ids=tuple(dict.fromkeys(claim_ids)),
            reviewer=_reviewer(reviewer),
            patient_body=patient_body,
            profile=profile.model_dump(mode="json"),
        )

    def transition_card(self, card_id: str, *, reviewer: str, target: str) -> None:
        self.store.transition_card(card_id, reviewer=_reviewer(reviewer), target=target)


def _reviewer(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("reviewer identity is required")
    return normalized
