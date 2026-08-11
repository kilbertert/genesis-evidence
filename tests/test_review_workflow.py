from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from genesis_evidence.core.store import Database, PaperStore, ReviewStore
from genesis_evidence.review.service import ClaimReviewInput, EvidenceReviewService


def _review_case(database: Database, *, integrity: str = "clear") -> tuple[str, str]:
    paper_id = str(uuid.uuid4())
    extraction_id = str(uuid.uuid4())
    claim_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO papers(id, title, integrity_status, created_at)
            VALUES (?, 'Vitamin D and frailty', ?, '2026-08-11T00:00:00Z')
            """,
            (paper_id, integrity),
        )
        connection.execute(
            """
            INSERT INTO paper_extractions(
                id, paper_id, model, extraction_run_id, extraction_json,
                check_model, check_run_id, consistency_status, consistency_json, created_at
            ) VALUES (?, ?, 'model', 'extract-run', '{}', 'model', 'check-run',
                'consistent', '{}', '2026-08-11T00:00:00Z')
            """,
            (extraction_id, paper_id),
        )
        connection.execute(
            """
            INSERT INTO claims(
                id, paper_id, extraction_id, candidate_text, evidence_text,
                locator, candidate_study_design, created_at
            ) VALUES (?, ?, ?, ?, ?, 'Results', 'cohort_study', '2026-08-11T00:00:00Z')
            """,
            (
                claim_id,
                paper_id,
                extraction_id,
                "Lower vitamin D was associated with frailty.",
                "Lower 25(OH)D was associated with higher frailty prevalence.",
            ),
        )
        connection.execute(
            "INSERT INTO paper_admissions(paper_id, status) VALUES (?, 'pending')",
            (paper_id,),
        )
    return paper_id, claim_id


def _service(tmp_path) -> tuple[Database, EvidenceReviewService]:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    return database, EvidenceReviewService(ReviewStore(database))


def test_paper_requires_clear_integrity_before_internal_admission(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, integrity="unknown")
    with pytest.raises(ValueError, match="integrity"):
        service.admit_paper(
            paper_id,
            reviewer="reviewer-1",
            condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        )


def test_observational_claim_cannot_be_approved_as_causal() -> None:
    with pytest.raises(ValidationError, match="cannot be approved as causal"):
        ClaimReviewInput(
            decision="approved",
            corrected_text="Vitamin D caused lower frailty.",
            corrected_study_design="cohort_study",
            inference="causal",
            grade="low",
            condition_code="COND_VITAMIN_D_DEFICIENCY",
        )


def test_claim_condition_must_match_paper_admission(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    with pytest.raises(ValueError, match="not selected"):
        service.review_claim(
            claim_id,
            reviewer="reviewer-1",
            review=ClaimReviewInput(
                decision="approved",
                corrected_text="Lower vitamin D status was associated with frailty.",
                corrected_study_design="cohort_study",
                inference="associational",
                grade="low",
                condition_code="COND_SARCOPENIA_FRAILTY",
            ),
        )


def test_one_reviewer_can_publish_a_traceable_card(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=ClaimReviewInput(
            decision="approved",
            corrected_text="Lower vitamin D status was associated with frailty.",
            corrected_study_design="cohort_study",
            inference="associational",
            grade="low",
            condition_code="COND_VITAMIN_D_DEFICIENCY",
        ),
    )
    card_id = service.create_card_draft(
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联，结果需要结合个人检查理解。",
    )
    for target in ("in_review", "approved"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
        assert ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY") == []
    service.transition_card(card_id, reviewer="reviewer-1", target="published")

    cards = ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY")
    assert [card["id"] for card in cards] == [card_id]
    with database.connect() as connection:
        admission = connection.execute(
            "SELECT status, condition_codes_json FROM paper_admissions"
        ).fetchone()
        assert admission["status"] == "internally_admitted"
        assert json.loads(admission["condition_codes_json"]) == [
            "COND_VITAMIN_D_DEFICIENCY"
        ]
        assert connection.execute("SELECT grade FROM knowledge_cards").fetchone()[0] == "low"


def test_non_published_and_stale_cards_are_invisible_to_patient_queries(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=ClaimReviewInput(
            decision="approved",
            corrected_text="Lower vitamin D status was associated with frailty.",
            corrected_study_design="cohort_study",
            inference="associational",
            grade="low",
            condition_code="COND_VITAMIN_D_DEFICIENCY",
        ),
    )
    card_id = service.create_card_draft(
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
    )
    assert ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY") == []
    for target in ("in_review", "approved", "published"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    PaperStore(database).update_integrity(
        paper_id,
        "corrected",
        detail={"source": "test"},
    )
    assert ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY") == []


def test_patient_card_body_rejects_diagnostic_wording(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=ClaimReviewInput(
            decision="approved",
            corrected_text="Lower vitamin D status was associated with frailty.",
            corrected_study_design="cohort_study",
            inference="associational",
            grade="low",
            condition_code="COND_VITAMIN_D_DEFICIENCY",
        ),
    )
    with pytest.raises(ValueError, match="forbidden term"):
        service.create_card_draft(
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="这是疾病诊断结果。",
        )


def test_rejecting_reviewed_evidence_stales_a_published_card(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    approved = ClaimReviewInput(
        decision="approved",
        corrected_text="Lower vitamin D status was associated with frailty.",
        corrected_study_design="cohort_study",
        inference="associational",
        grade="low",
        condition_code="COND_VITAMIN_D_DEFICIENCY",
    )
    service.review_claim(claim_id, reviewer="reviewer-1", review=approved)
    card_id = service.create_card_draft(
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
    )
    for target in ("in_review", "approved", "published"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=ClaimReviewInput(decision="rejected"),
    )
    assert ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY") == []
