from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from genesis_evidence.core.contracts import (
    ClaimReference,
    GradeRecord,
    KnowledgeCardPayload,
    PatientFinding,
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
