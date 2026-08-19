from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from genesis_evidence.core.contracts import (
    ClaimReference,
    EvidenceMatchObservation,
    GradeRecord,
    KnowledgeCardPayload,
    PatientFinding,
    PublishedEvidenceCard,
)


def _card(**overrides):
    now = datetime.now(UTC)
    values = {
        "condition_code": "COND_DYSLIPIDEMIA",
        "version": "1.0.0",
        "status": "published",
        "claims": [
            ClaimReference(
                claim_id="claim-1",
                paper_id="paper-1",
                evidence="The paper directly reports this result.",
                locator="p. 4",
            )
        ],
        "grade": GradeRecord(rating="moderate", reviewer="reviewer-1", rated_at=now),
        "reviewer": "reviewer-1",
        "reviewed_at": now,
        "published_at": now,
        "patient_visible_body": "这是经审核的营养相关知识内容。",
    }
    values.update(overrides)
    return KnowledgeCardPayload.model_validate(values)


def test_published_card_requires_sources_review_and_patient_body() -> None:
    assert _card().status == "published"
    with pytest.raises(ValidationError):
        _card(claims=[])
    with pytest.raises(ValidationError):
        _card(published_at=None)
    with pytest.raises(ValidationError):
        _card(patient_visible_body="")


def test_patient_finding_requires_a_traceable_card() -> None:
    with pytest.raises(ValidationError):
        PatientFinding(
            condition_code="COND_DYSLIPIDEMIA",
            card_id="",
            card_version="1.0.0",
            urgency="routine",
            abnormality_severity=1,
            evidence_strength="moderate",
            needs_recheck=True,
            department="心血管内科",
        )


def test_observation_bbox_is_bounded_and_ordered() -> None:
    observation = EvidenceMatchObservation(
        observation_id="observation-1",
        confirmation_status="confirmed",
        metric_code="fasting_glucose",
        value=6.8,
        unit="mmol/L",
        reference_low=3.9,
        reference_high=6.1,
        evidence_text="空腹血糖 6.8 mmol/L 3.9-6.1",
        source_file_index=1,
        source_page=1,
        bbox_normalized=[10, 20, 100, 120],
    )
    assert observation.bbox_normalized == [10, 20, 100, 120]
    with pytest.raises(ValidationError):
        EvidenceMatchObservation(
            **observation.model_dump(exclude={"bbox_normalized"}),
            bbox_normalized=[100, 20, 10, 120],
        )


def test_published_evidence_card_requires_a_claim_source() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        PublishedEvidenceCard(
            id="card-1",
            condition_code="COND_DYSLIPIDEMIA",
            scope_key="metric:ldl_c",
            version="1.0.0",
            status="published",
            grade="moderate",
            published_at=now,
            evidence_profile_id="profile-1",
            patient_visible_body="内容",
            sources=[],
        )
