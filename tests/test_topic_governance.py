from __future__ import annotations

import uuid

import pytest

from genesis_evidence.core.store import Database, PaperStore, ReviewStore
from genesis_evidence.review.service import EvidenceReviewService

from .test_review_workflow import (
    _approved_review,
    _profile,
    _review_case,
)


def _topic(store: PaperStore, *, lock: bool = True, streams: tuple[str, ...] = ("effect",)):
    topic_id = store.create_topic(
        code="vitamin-d-frailty",
        version="1",
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        review_question="Is vitamin D status associated with frailty in older adults?",
        picots={
            "population": "Older adults",
            "intervention_or_exposure": "Serum 25(OH)D",
            "comparator": "Higher versus lower status",
            "outcomes": "Frailty",
            "timing": "Baseline",
            "setting": "Any human setting",
        },
        eligible_study_designs=("cohort_study",),
        inclusion_criteria=("Human older adults",),
        exclusion_reasons=("wrong_population", "wrong_exposure", "wrong_outcome"),
        required_search_streams=streams,
        evidence_cutoff_date="2026-08-12",
        reviewer="reviewer-1",
    )
    if lock:
        store.lock_topic(topic_id, reviewer="reviewer-1")
    return topic_id


def test_collection_requires_a_locked_versioned_topic(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store, lock=False)
    with pytest.raises(ValueError, match="locked evidence topic"):
        store.start_collection(topic_id=topic_id, source="test", query="vitamin D")


def test_topic_rejects_a_future_evidence_cutoff(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    with pytest.raises(ValueError, match="cannot be in the future"):
        store.create_topic(
            code="future-topic",
            version="1",
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            review_question="Future question",
            picots={
                "population": "Older adults",
                "intervention_or_exposure": "Vitamin D",
                "comparator": "Comparator",
                "outcomes": "Frailty",
                "timing": "Baseline",
                "setting": "Any",
            },
            eligible_study_designs=("cohort_study",),
            inclusion_criteria=("Human",),
            exclusion_reasons=("wrong_population",),
            required_search_streams=("effect",),
            evidence_cutoff_date="2099-01-01",
            reviewer="reviewer-1",
        )


def test_exclusion_requires_one_catalogued_primary_reason(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    paper_id, _ = _review_case(database)
    run_id = store.start_collection(topic_id=topic_id, source="test", query="vitamin D")
    store.add_to_collection(run_id, paper_id, position=1)
    store.finish_collection(run_id, status="completed", detail={})
    with pytest.raises(ValueError, match="catalogued primary reason"):
        store.screen_collection_paper(
            run_id,
            paper_id,
            stage="title_abstract",
            decision="excluded",
            exclusion_reason="not_in_the_protocol",
            reviewer="reviewer-1",
        )


def test_profile_rejects_incomplete_required_streams_and_screening(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_store = PaperStore(database)
    topic_id = _topic(paper_store, streams=("effect", "safety"))
    paper_id, claim_id = _review_case(database)
    run_id = paper_store.start_collection(
        topic_id=topic_id, source="test", query="vitamin D", search_stream="effect"
    )
    paper_store.add_to_collection(run_id, paper_id, position=1)
    paper_store.finish_collection(run_id, status="completed", detail={})
    service = EvidenceReviewService(ReviewStore(database))
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        differences_confirmed=False,
        study_design="cohort_study",
        publication_role="primary",
        identity_confirmed=True,
    )
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    with pytest.raises(ValueError, match="required search stream"):
        service.create_card_draft(
            topic_id=topic_id,
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="维生素 D 状态与衰弱之间存在研究关联。",
            profile=_profile(claim_id),
        )


def test_profile_rejects_an_incomplete_screening_ledger(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_store = PaperStore(database)
    topic_id = _topic(paper_store)
    paper_id, claim_id = _review_case(database)
    run_id = paper_store.start_collection(topic_id=topic_id, source="test", query="vitamin D")
    paper_store.add_to_collection(run_id, paper_id, position=1)
    paper_store.finish_collection(run_id, status="completed", detail={})
    service = EvidenceReviewService(ReviewStore(database))
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        differences_confirmed=False,
        study_design="cohort_study",
        publication_role="primary",
        identity_confirmed=True,
    )
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())
    with pytest.raises(ValueError, match="screening ledger"):
        service.create_card_draft(
            topic_id=topic_id,
            condition_code="COND_VITAMIN_D_DEFICIENCY",
            version="1.0.0",
            claim_ids=[claim_id],
            reviewer="reviewer-1",
            patient_body="维生素 D 状态与衰弱之间存在研究关联。",
            profile=_profile(claim_id),
        )


def test_not_retrieved_report_closes_ledger_without_scientific_exclusion(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    included_paper, claim_id = _review_case(database)
    missing_paper = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, created_at) VALUES (?, 'Unavailable paper', 'now')",
            (missing_paper,),
        )
        connection.execute(
            "INSERT INTO paper_sources(paper_id, source, source_id, source_url) "
            "VALUES (?, 'test', ?, 'https://example.test/unavailable')",
            (missing_paper, missing_paper),
        )
        connection.execute(
            "INSERT INTO paper_admissions(paper_id, status) VALUES (?, 'pending')",
            (missing_paper,),
        )
    from .test_review_workflow import _complete_topic

    topic_id = _complete_topic(database, included_paper)
    paper_store = PaperStore(database)
    run_id = paper_store.start_collection(topic_id=topic_id, source="test", query="missing")
    paper_store.add_to_collection(run_id, missing_paper, position=1)
    paper_store.finish_collection(run_id, status="completed", detail={})
    paper_store.screen_collection_paper(
        run_id,
        missing_paper,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="reviewer-1",
    )
    paper_store.record_full_text_retrieval(
        run_id,
        missing_paper,
        status="not_retrieved",
        reason="Repository exposes metadata but no legally retrievable full text.",
        reviewer="reviewer-1",
    )
    service = EvidenceReviewService(ReviewStore(database), paper_store)
    terminal = service.auto_review_paper(
        missing_paper, requested_by="authenticated-reviewer"
    )
    assert terminal["status"] == "completed"
    assert terminal["decision"] == "not_retrieved"
    queue_item = next(
        row for row in ReviewStore(database).list_review_queue() if row["id"] == missing_paper
    )
    assert queue_item["review_state"] == "completed"
    assert queue_item["full_text_not_retrieved"] == 1
    detail = ReviewStore(database).get_review_item(missing_paper)
    assert detail is not None
    assert detail["review_guidance"]["terminal_decision"] == "not_retrieved"
    assert detail["review_guidance"]["state"] == "completed"
    service.admit_paper(
        included_paper,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        differences_confirmed=False,
        study_design="cohort_study",
        publication_role="primary",
        identity_confirmed=True,
    )
    service.review_claim(claim_id, reviewer="reviewer-1", review=_approved_review())

    card_id = service.create_card_draft(
        topic_id=topic_id,
        condition_code="COND_VITAMIN_D_DEFICIENCY",
        version="1.0.0",
        claim_ids=[claim_id],
        reviewer="reviewer-1",
        patient_body="维生素 D 状态与衰弱之间存在研究关联。",
        profile=_profile(claim_id),
    )
    ledger = paper_store.list_topic_ledger(topic_id)
    missing = next(row for row in ledger if row["paper_id"] == missing_paper)
    assert missing["full_text_retrieval_status"] == "not_retrieved"
    assert missing["primary_exclusion_reason"] is None
    assert card_id
