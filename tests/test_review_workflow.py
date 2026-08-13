from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from genesis_evidence.core.store import Database, PaperStore, ReviewStore
from genesis_evidence.review.service import (
    ClaimReviewInput,
    EvidenceProfileInput,
    EvidenceReviewService,
    RiskOfBiasInput,
)


def _review_case(
    database: Database,
    *,
    integrity: str = "clear",
    consistency: str = "consistent",
    publication_status: str = "formal",
) -> tuple[str, str]:
    paper_id = str(uuid.uuid4())
    extraction_id = str(uuid.uuid4())
    study_id = str(uuid.uuid4())
    result_id = str(uuid.uuid4())
    claim_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO papers(id, title, publication_status, integrity_status, created_at)
            VALUES (?, 'Vitamin D and frailty', ?, ?, '2026-08-11T00:00:00Z')
            """,
            (paper_id, publication_status, integrity),
        )
        connection.execute(
            """
            INSERT INTO paper_extractions(
                id, paper_id, model, extraction_run_id, extraction_json,
                second_model, second_run_id, second_extraction_json,
                check_model, check_run_id, consistency_status, consistency_json, created_at
            ) VALUES (?, ?, 'model-a', 'extract-run-a', '{}', 'model-b',
                'extract-run-b', '{}', 'checker', 'check-run', ?, '{}',
                '2026-08-11T00:00:00Z')
            """,
            (extraction_id, paper_id, consistency),
        )
        connection.execute(
            """
            INSERT INTO studies(id, study_design, created_at)
            VALUES (?, 'cohort_study', '2026-08-11T00:00:00Z')
            """,
            (study_id,),
        )
        connection.execute(
            "INSERT INTO study_publications(study_id, paper_id) VALUES (?, ?)",
            (study_id, paper_id),
        )
        connection.execute(
            """
            INSERT INTO results(
                id, study_id, paper_id, extraction_id, population,
                baseline_nutrient_status, ingredient_name, ingredient_form,
                dose, comparator, outcome, timepoint, effect_estimate,
                statistical_details, evidence_text, locator, created_at
            ) VALUES (?, ?, ?, ?, 'Adults aged 60 years and older',
                'Measured serum 25(OH)D', 'Vitamin D', '25(OH)D status',
                'Not applicable', 'Higher versus lower status', 'Frailty prevalence',
                'Baseline', 'Higher prevalence', 'Adjusted association reported',
                'Lower 25(OH)D was associated with higher frailty prevalence.',
                'Results', '2026-08-11T00:00:00Z')
            """,
            (result_id, study_id, paper_id, extraction_id),
        )
        connection.execute(
            """
            INSERT INTO claims(
                id, paper_id, extraction_id, result_id, candidate_text,
                evidence_text, locator, candidate_study_design, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'Results', 'cohort_study',
                '2026-08-11T00:00:00Z')
            """,
            (
                claim_id,
                paper_id,
                extraction_id,
                result_id,
                "Lower vitamin D was associated with frailty.",
                "Lower 25(OH)D was associated with higher frailty prevalence.",
            ),
        )
        connection.execute(
            "INSERT INTO paper_admissions(paper_id, status) VALUES (?, 'pending')",
            (paper_id,),
        )
    return paper_id, claim_id


def _approved_review(condition_code: str = "COND_VITAMIN_D_DEFICIENCY") -> ClaimReviewInput:
    return ClaimReviewInput(
        decision="approved",
        corrected_text="Lower vitamin D status was associated with frailty.",
        corrected_study_design="cohort_study",
        inference="associational",
        risk_of_bias=RiskOfBiasInput(
            tool="exposure_study",
            overall="some_concerns",
            rationale="Residual confounding remains possible.",
        ),
        applicability="Applies to older adults with measured serum 25(OH)D.",
        condition_code=condition_code,
    )


def _profile(claim_id: str, *, certainty: str = "moderate") -> EvidenceProfileInput:
    return EvidenceProfileInput(
        certainty=certainty,
        certainty_rationale="The complete eligible evidence body was reviewed for this outcome.",
        estimate_target="Association between baseline 25(OH)D status and frailty prevalence",
        interpretations={claim_id: "supports"},
    )


def _complete_topic(database: Database, *paper_ids: str) -> str:
    store = PaperStore(database)
    topic_id = store.create_topic(
        code=f"vitamin-d-frailty-{uuid.uuid4()}",
        version="1",
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        review_question="Is vitamin D status associated with frailty in older adults?",
        picots={
            "population": "Adults aged 60 years and older",
            "intervention_or_exposure": "Measured serum 25(OH)D",
            "comparator": "Higher versus lower status",
            "outcomes": "Frailty prevalence",
            "timing": "Baseline",
            "setting": "Any human setting",
        },
        eligible_study_designs=("cohort_study",),
        inclusion_criteria=("Older adults with measured serum 25(OH)D",),
        exclusion_reasons=("wrong_population", "wrong_exposure", "wrong_outcome"),
        required_search_streams=("effect",),
        evidence_cutoff_date="2026-08-12",
        reviewer="reviewer-1",
    )
    store.lock_topic(topic_id, reviewer="reviewer-1")
    run_id = store.start_collection(
        topic_id=topic_id,
        source="test",
        query="vitamin D AND frailty",
    )
    for position, paper_id in enumerate(paper_ids, 1):
        store.add_to_collection(run_id, paper_id, position=position)
    store.finish_collection(run_id, status="completed", detail={})
    for paper_id in paper_ids:
        store.screen_collection_paper(
            run_id,
            paper_id,
            stage="title_abstract",
            decision="included",
            exclusion_reason=None,
            reviewer="reviewer-1",
        )
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO full_texts(paper_id, object_key, sha256, media_type, rights_status)
                VALUES (?, ?, ?, 'application/xml', 'redistributable')
                ON CONFLICT(paper_id) DO NOTHING
                """,
                (paper_id, f"test/{paper_id}.xml", "0" * 64),
            )
        store.screen_collection_paper(
            run_id,
            paper_id,
            stage="full_text",
            decision="included",
            exclusion_reason=None,
            reviewer="reviewer-1",
        )
    return topic_id


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
            risk_of_bias=RiskOfBiasInput(
                tool="exposure_study", overall="some_concerns", rationale="Confounding."
            ),
            applicability="Older adults only.",
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
                risk_of_bias=RiskOfBiasInput(
                    tool="exposure_study", overall="some_concerns", rationale="Confounding."
                ),
                applicability="Older adults only.",
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
        review=_approved_review(),
    )
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联，结果需要结合个人检查理解。",
        profile=_profile(claim_id),
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
        assert json.loads(admission["condition_codes_json"]) == ["COND_VITAMIN_D_DEFICIENCY"]
        assert connection.execute("SELECT grade FROM knowledge_cards").fetchone()[0] == "moderate"


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
        review=_approved_review(),
    )
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(claim_id),
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


def test_ai_differences_require_human_resolution_before_admission(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database, consistency="needs_review")
    with pytest.raises(ValueError, match="human resolution"):
        service.admit_paper(
            paper_id,
            reviewer="reviewer-1",
            condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        )
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        consistency_resolution="The locator was corrected against the full text.",
    )


def test_low_certainty_benefit_card_cannot_be_patient_visible(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="研究结论仍然不确定。",
        profile=_profile(claim_id, certainty="low"),
    )
    for target in ("in_review", "approved"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    with pytest.raises(ValueError, match="high or moderate"):
        service.transition_card(card_id, reviewer="reviewer-1", target="published")


def test_claim_without_structured_result_cannot_be_admitted(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, _ = _review_case(database)
    with database.transaction() as connection:
        connection.execute("DELETE FROM claims WHERE paper_id = ?", (paper_id,))
        connection.execute("DELETE FROM results WHERE paper_id = ?", (paper_id,))
    with pytest.raises(ValueError, match="structured Result"):
        service.admit_paper(
            paper_id,
            reviewer="reviewer-1",
            condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        )


def test_preprint_cannot_support_patient_visible_profile(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE papers SET publication_status = 'preprint' WHERE id = ?", (paper_id,)
        )
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    with pytest.raises(ValueError, match="formal publications"):
        service.create_card_draft(
            topic_id=_complete_topic(database, paper_id),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="研究结论仍需正式发表后确认。",
            profile=_profile(claim_id),
        )


def test_unknown_publication_status_cannot_support_patient_visible_profile(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database, publication_status="unknown")
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    with pytest.raises(ValueError, match="formal publications"):
        service.create_card_draft(
            topic_id=_complete_topic(database, paper_id),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="论文发表状态仍需核验。",
            profile=_profile(claim_id),
        )


def test_unknown_publication_does_not_block_complete_formal_evidence_profile(tmp_path) -> None:
    database, service = _service(tmp_path)
    formal_paper, formal_claim = _review_case(database)
    unknown_paper, _ = _review_case(database, publication_status="unknown")
    for paper_id in (formal_paper, unknown_paper):
        service.admit_paper(
            paper_id,
            reviewer="reviewer-1",
            condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        )
    service.review_claim(formal_claim, reviewer="reviewer-1", review=_approved_review())
    service.create_card_draft(
        topic_id=_complete_topic(database, formal_paper),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[formal_claim],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(formal_claim),
    )


def test_publish_rechecks_publication_status_after_card_draft(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(claim_id),
    )
    for target in ("in_review", "approved"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE papers SET publication_status = 'unknown' WHERE id = ?", (paper_id,)
        )
    with pytest.raises(ValueError, match="ineligible evidence"):
        service.transition_card(card_id, reviewer="reviewer-1", target="published")


@pytest.mark.parametrize("study_design", ["case_report", "case_series"])
def test_case_reports_cannot_support_patient_visible_profile(tmp_path, study_design) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE papers SET study_design_candidate = ? WHERE id = ?",
            (study_design, paper_id),
        )
        connection.execute(
            "UPDATE claims SET candidate_study_design = ?, candidate_claim_type = 'safety' "
            "WHERE id = ?",
            (study_design, claim_id),
        )
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
            corrected_text="This report records a possible safety signal.",
            corrected_study_design=study_design,
            inference="descriptive",
            risk_of_bias=RiskOfBiasInput(
                tool="safety_signal",
                overall="high",
                rationale="A case report cannot estimate incidence or establish causality.",
            ),
            applicability="Safety signal only.",
            condition_code="COND_VITAMIN_D_DEFICIENCY",
        ),
    )
    with pytest.raises(ValueError, match="case-report"):
        service.create_card_draft(
            topic_id=_complete_topic(database, paper_id),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="研究结果仅作为安全信号记录。",
            profile=_profile(claim_id),
        )


def test_unreviewed_eligible_result_blocks_evidence_profile(tmp_path) -> None:
    database, service = _service(tmp_path)
    first_paper, first_claim = _review_case(database)
    second_paper, _ = _review_case(database)
    for paper_id in (first_paper, second_paper):
        service.admit_paper(
            paper_id,
            reviewer="reviewer-1",
            condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        )
    service.review_claim(first_claim, reviewer="reviewer-1", review=_approved_review())
    with pytest.raises(ValueError, match="full-text included paper"):
        service.create_card_draft(
            topic_id=_complete_topic(database, first_paper, second_paper),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[first_claim],
            reviewer="reviewer-1",
            patient_body="维生素 D 状态与衰弱之间存在研究关联。",
            profile=_profile(first_claim),
        )


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
        review=_approved_review(),
    )
    with pytest.raises(ValueError, match="forbidden term"):
        service.create_card_draft(
            topic_id=_complete_topic(database, paper_id),
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="这是疾病诊断结果。",
            profile=_profile(claim_id),
        )


def test_rejecting_reviewed_evidence_stales_a_published_card(tmp_path) -> None:
    database, service = _service(tmp_path)
    paper_id, claim_id = _review_case(database)
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
    )
    approved = _approved_review()
    service.review_claim(claim_id, reviewer="reviewer-1", review=approved)
    card_id = service.create_card_draft(
        topic_id=_complete_topic(database, paper_id),
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(claim_id),
    )
    for target in ("in_review", "approved", "published"):
        service.transition_card(card_id, reviewer="reviewer-1", target=target)
    service.review_claim(
        claim_id,
        reviewer="reviewer-1",
        review=ClaimReviewInput(decision="rejected"),
    )
    assert ReviewStore(database).list_published_cards("COND_VITAMIN_D_DEFICIENCY") == []
