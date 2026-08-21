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


def test_locked_topic_can_extend_collection_after_profile_creation(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO evidence_profiles(
                id, topic_id, condition_code, version, ingredient_name, ingredient_form,
                population, baseline_nutrient_status, dose, comparator, outcome, timepoint,
                estimate_target, evidence_body_complete, certainty, certainty_rationale,
                evidence_cutoff_date, reviewer, reviewed_at, created_at
            ) VALUES (?, ?, 'COND_VITAMIN_D_DEFICIENCY', '1.0.0', 'Vitamin D', 'status',
                'Older adults', 'Not reported', 'Not applicable', 'Higher versus lower',
                'Frailty', 'Baseline', 'Frailty prevalence', 1, 'low', 'Initial profile',
                '2026-08-12', 'reviewer-1', '2026-08-12T00:00:00Z', '2026-08-12T00:00:00Z')
            """,
            (str(uuid.uuid4()), topic_id),
        )
    run_id = store.start_collection(topic_id=topic_id, source="test", query="vitamin D update")
    assert run_id


def test_new_collection_after_profile_creation_remains_screenable(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO evidence_profiles(
                id, topic_id, condition_code, version, ingredient_name, ingredient_form,
                population, baseline_nutrient_status, dose, comparator, outcome, timepoint,
                estimate_target, evidence_body_complete, certainty, certainty_rationale,
                evidence_cutoff_date, reviewer, reviewed_at, created_at
            ) VALUES (?, ?, 'COND_VITAMIN_D_DEFICIENCY', '1.0.0', 'Vitamin D', 'status',
                'Older adults', 'Not reported', 'Not applicable', 'Higher versus lower',
                'Frailty', 'Baseline', 'Frailty prevalence', 1, 'low', 'Initial profile',
                '2026-08-12', 'reviewer-1', '2026-08-12T00:00:00Z', '2026-08-12T00:00:00Z')
            """,
            (str(uuid.uuid4()), topic_id),
        )
    paper_id, _ = _review_case(database, screened=False)
    run_id = store.start_collection(topic_id=topic_id, source="test", query="new batch")
    store.add_to_collection(run_id, paper_id, position=1)
    store.finish_collection(run_id, status="completed", detail={})
    store.screen_collection_paper(
        run_id,
        paper_id,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="reviewer-1",
    )


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
    paper_id, _ = _review_case(database, screened=False)
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
    terminal = service.auto_review_paper(missing_paper, requested_by="authenticated-reviewer")
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


def test_ai_closes_title_abstract_exclusion_without_full_text_or_admission_row(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_store = PaperStore(database)
    topic_id = _topic(paper_store)
    paper_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, publication_status, integrity_status, created_at) "
            "VALUES (?, 'Title abstract exclusion', 'formal', 'clear', 'now')",
            (paper_id,),
        )
        connection.execute(
            "INSERT INTO paper_sources(paper_id, source, source_id, source_url) "
            "VALUES (?, 'test', ?, 'https://example.test/excluded')",
            (paper_id, paper_id),
        )
    run_id = paper_store.start_collection(topic_id=topic_id, source="test", query="excluded")
    paper_store.add_to_collection(run_id, paper_id, position=1)
    paper_store.finish_collection(run_id, status="completed", detail={})
    paper_store.screen_collection_paper(
        run_id,
        paper_id,
        stage="title_abstract",
        decision="excluded",
        exclusion_reason="wrong_population",
        reviewer="reviewer-1",
    )

    result = EvidenceReviewService(ReviewStore(database), paper_store).auto_review_paper(
        paper_id, requested_by="authenticated-reviewer"
    )

    assert result == {"status": "completed", "decision": "excluded", "cards": []}
    with database.connect() as connection:
        admission = connection.execute(
            "SELECT status, reviewer FROM paper_admissions WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        assert tuple(admission) == ("rejected", "ai:screening-ledger")
        assert (
            connection.execute(
                "SELECT count(*) FROM paper_extraction_jobs WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            == 0
        )


def test_screening_and_retrieval_propagate_across_runs_for_one_topic(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    paper_id, _ = _review_case(database, screened=False)
    first_run = store.start_collection(topic_id=topic_id, source="test", query="one")
    second_run = store.start_collection(topic_id=topic_id, source="test", query="two")
    for run_id in (first_run, second_run):
        store.add_to_collection(run_id, paper_id, position=1)
        store.finish_collection(run_id, status="completed", detail={})

    store.screen_collection_paper(
        first_run,
        paper_id,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="reviewer-1",
    )
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO full_texts(paper_id, object_key, sha256, media_type, rights_status) "
            "VALUES (?, 'objects/paper.xml', 'hash', 'application/xml', 'internal_tdm_only')",
            (paper_id,),
        )
    store.record_full_text_retrieval(
        first_run,
        paper_id,
        status="retrieved",
        reason=None,
        reviewer="reviewer-1",
    )

    rows = store.list_topic_ledger(topic_id)
    assert {row["run_id"] for row in rows} == {first_run, second_run}
    assert all(row["title_abstract_decision"] == "included" for row in rows)
    assert all(row["full_text_retrieval_status"] == "retrieved" for row in rows)


def test_reconcile_topic_ledger_reports_real_conflicts_without_guessing(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    paper_id, _ = _review_case(database)
    first_run = store.start_collection(topic_id=topic_id, source="test", query="one")
    second_run = store.start_collection(topic_id=topic_id, source="test", query="two")
    for run_id in (first_run, second_run):
        store.add_to_collection(run_id, paper_id, position=1)
        store.finish_collection(run_id, status="completed", detail={})
    store.screen_collection_paper(
        first_run,
        paper_id,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="reviewer-1",
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE collection_papers SET title_abstract_decision = 'excluded', "
            "primary_exclusion_reason = 'wrong_population' WHERE run_id = ? AND paper_id = ?",
            (second_run, paper_id),
        )

    result = store.reconcile_topic_ledger(topic_id, reviewer="ai:ledger-reconciler")

    assert result["normalized_records"] == 0
    assert result["conflicts"] == [
        {
            "paper_id": paper_id,
            "title_abstract_decisions": ["excluded", "included"],
            "full_text_decisions": [],
        }
    ]
    rows = store.list_topic_ledger(topic_id)
    assert {row["title_abstract_decision"] for row in rows} == {"included", "excluded"}


def test_reconcile_topic_ledger_copies_one_known_decision_to_duplicates(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    paper_id, _ = _review_case(database)
    first_run = store.start_collection(topic_id=topic_id, source="test", query="one")
    second_run = store.start_collection(topic_id=topic_id, source="test", query="two")
    for run_id in (first_run, second_run):
        store.add_to_collection(run_id, paper_id, position=1)
        store.finish_collection(run_id, status="completed", detail={})
    store.screen_collection_paper(
        first_run,
        paper_id,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="reviewer-1",
    )

    result = store.reconcile_topic_ledger(topic_id, reviewer="ai:ledger-reconciler")

    assert result["conflicts"] == []
    assert result["normalized_records"] == 2
    assert all(
        row["title_abstract_decision"] == "included" for row in store.list_topic_ledger(topic_id)
    )


def test_reconcile_topic_ledger_preserves_title_exclusion_reason(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    paper_id, _ = _review_case(database)
    first_run = store.start_collection(topic_id=topic_id, source="test", query="one")
    second_run = store.start_collection(topic_id=topic_id, source="test", query="two")
    for run_id in (first_run, second_run):
        store.add_to_collection(run_id, paper_id, position=1)
        store.finish_collection(run_id, status="completed", detail={})
    store.screen_collection_paper(
        first_run,
        paper_id,
        stage="title_abstract",
        decision="excluded",
        exclusion_reason="wrong_population",
        reviewer="reviewer-1",
    )

    result = store.reconcile_topic_ledger(topic_id, reviewer="ai:ledger-reconciler")

    assert result["conflicts"] == []
    assert all(
        (row["title_abstract_decision"], row["primary_exclusion_reason"])
        == ("excluded", "wrong_population")
        for row in store.list_topic_ledger(topic_id)
    )
