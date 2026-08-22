from __future__ import annotations

from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database, PaperStore, ReviewStore
from genesis_evidence.review.api import create_app

from .test_report_assessment import _publish_card

API_KEY = "test-review-key-with-32-characters"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def test_coverage_matrix_expands_first_batch_metrics(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()

    matrix = ReviewStore(database).list_coverage_matrix()

    assert len(matrix) == 33
    assert {row["condition_code"] for row in matrix} == {
        "COND_HYPERTENSION_RISK",
        "COND_PREDIABETES",
        "COND_DYSLIPIDEMIA",
        "COND_MASLD_RISK",
        "COND_HYPERURICEMIA_RISK",
        "COND_CKD_RISK",
        "COND_ANEMIA_PATTERN",
        "COND_VITAMIN_D_DEFICIENCY",
        "COND_OSTEOPOROSIS_RISK",
        "COND_SARCOPENIA_FRAILTY",
        "COND_MALNUTRITION_RISK",
        "COND_CHRONIC_CONSTIPATION",
    }
    ldl = next(row for row in matrix if row["metric_code"] == "ldl_c")
    assert ldl["coverage_status"] == "planned"
    assert ldl["next_action"] == "建立并锁定版本化主题/PICOTS"


def test_coverage_matrix_reports_published_metric_and_api_auth(tmp_path) -> None:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    _publish_card(database, "COND_PREDIABETES", grade="moderate")
    client = TestClient(create_app(database_path=path, api_key=API_KEY, reviewer_id="reviewer"))

    assert client.get("/api/review/coverage-matrix").status_code == 401
    response = client.get("/api/review/coverage-matrix", headers=HEADERS)

    assert response.status_code == 200
    row = next(
        item
        for item in response.json()
        if item["condition_code"] == "COND_PREDIABETES" and item["metric_code"] == "fasting_glucose"
    )
    assert row["coverage_status"] == "published"
    assert row["published_card"]["status"] == "published"
    assert row["published_card"]["grade"] == "moderate"
    assert row["cards"]["published"] == 1


def test_combined_blood_pressure_topic_covers_both_metrics(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = store.create_topic(
        code="sodium-blood-pressure",
        version="1",
        condition_code="COND_HYPERTENSION_RISK",
        review_question="Does sodium reduction lower blood pressure?",
        picots={
            "population": "Adults aged 40 and older",
            "intervention_or_exposure": "Dietary sodium reduction",
            "comparator": "Usual sodium intake",
            "outcomes": "Systolic and diastolic blood pressure",
            "timing": "At least 4 weeks",
            "setting": "Community or clinical",
        },
        eligible_study_designs=("randomized_controlled_trial",),
        inclusion_criteria=("Adults with reported blood pressure",),
        exclusion_reasons=("wrong_population", "wrong_outcome"),
        required_search_streams=("effect",),
        evidence_cutoff_date="2026-08-20",
        reviewer="reviewer",
    )
    store.lock_topic(topic_id, reviewer="reviewer")

    hypertension = [
        row
        for row in ReviewStore(database).list_coverage_matrix()
        if row["condition_code"] == "COND_HYPERTENSION_RISK"
    ]

    assert {row["metric_code"] for row in hypertension} == {
        "systolic_blood_pressure",
        "diastolic_blood_pressure",
    }
    assert {row["coverage_status"] for row in hypertension} == {"topic_locked"}


def test_short_metric_abbreviations_do_not_match_words_by_substring(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = store.create_topic(
        code="fatty-liver-alternative-diet",
        version="1",
        condition_code="COND_MASLD_RISK",
        review_question="Does an alternative diet affect liver outcomes?",
        picots={
            "population": "Adults",
            "intervention_or_exposure": "Alternative diet",
            "comparator": "Usual diet",
            "outcomes": "Liver fat",
            "timing": "At least 8 weeks",
            "setting": "Outpatient",
        },
        eligible_study_designs=("randomized_controlled_trial",),
        inclusion_criteria=("Adults with liver fat outcomes",),
        exclusion_reasons=("wrong_outcome",),
        required_search_streams=("effect",),
        evidence_cutoff_date="2026-08-20",
        reviewer="reviewer",
    )
    store.lock_topic(topic_id, reviewer="reviewer")

    masld = [
        row
        for row in ReviewStore(database).list_coverage_matrix()
        if row["condition_code"] == "COND_MASLD_RISK"
    ]

    assert {row["coverage_status"] for row in masld} == {"planned"}


def test_screening_backlog_blocks_claim_ready_status(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = store.create_topic(
        code="prediabetes-screening-backlog",
        version="1",
        condition_code="COND_PREDIABETES",
        review_question="Does nutrition affect fasting glucose?",
        picots={
            "population": "Adults with prediabetes",
            "intervention_or_exposure": "Nutrition intervention",
            "comparator": "Usual diet",
            "outcomes": "Fasting glucose",
            "timing": "At least 4 weeks",
            "setting": "Community or clinical",
        },
        eligible_study_designs=("randomized_controlled_trial",),
        inclusion_criteria=("Human adults",),
        exclusion_reasons=("wrong_population", "wrong_outcome"),
        required_search_streams=("effect",),
        evidence_cutoff_date="2026-08-20",
        reviewer="reviewer",
    )
    store.lock_topic(topic_id, reviewer="reviewer")
    run_id = store.start_collection(
        topic_id=topic_id,
        source="test",
        query="prediabetes fasting glucose nutrition",
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO papers(id, title, publication_status, integrity_status, created_at)
            VALUES ('paper-pending', 'Pending screening', 'formal', 'clear',
                '2026-08-20T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO papers(id, title, publication_status, integrity_status, created_at)
            VALUES ('paper-excluded', 'Excluded after title screening', 'formal', 'clear',
                '2026-08-20T00:00:00Z')
            """
        )
    store.add_to_collection(run_id, "paper-pending", position=1)
    store.add_to_collection(run_id, "paper-excluded", position=2)
    store.finish_collection(run_id, status="completed", detail={})
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE collection_papers SET title_abstract_decision = 'excluded',
                title_abstract_reviewer = 'reviewer',
                title_abstract_reviewed_at = '2026-08-20T00:00:00Z',
                full_text_retrieval_status = 'retrieved',
                primary_exclusion_reason = 'wrong_population'
            WHERE paper_id = 'paper-excluded'
            """
        )

    row = next(
        item
        for item in ReviewStore(database).list_coverage_matrix()
        if item["condition_code"] == "COND_PREDIABETES" and item["metric_code"] == "fasting_glucose"
    )

    assert row["coverage_status"] == "screening"
    assert row["screening_backlog"] == {
        "title_abstract": 1,
        "retrieval": 0,
        "full_text": 0,
    }


def test_matrix_matches_long_form_uacr_and_bone_outcomes(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    for condition_code, code, outcomes in (
        (
            "COND_CKD_RISK",
            "ckd-uacr",
            "Adults with kidney disease risk; dietary sodium; usual diet; "
            "urine albumin-to-creatinine ratio; 12 weeks; outpatient",
        ),
        (
            "COND_OSTEOPOROSIS_RISK",
            "osteoporosis-bone-mineral-density",
            "Postmenopausal adults; calcium; placebo; bone mineral density, T-score, "
            "and calcium; 12 months; outpatient",
        ),
    ):
        topic_id = store.create_topic(
            code=code,
            version="1",
            condition_code=condition_code,
            review_question="Does the nutrition exposure affect the reported outcome?",
            picots={
                "population": outcomes.split("; ")[0],
                "intervention_or_exposure": outcomes.split("; ")[1],
                "comparator": outcomes.split("; ")[2],
                "outcomes": outcomes.split("; ")[3],
                "timing": outcomes.split("; ")[4],
                "setting": outcomes.split("; ")[5],
            },
            eligible_study_designs=("randomized_controlled_trial",),
            inclusion_criteria=("Human adults",),
            exclusion_reasons=("wrong_outcome",),
            required_search_streams=("effect",),
            evidence_cutoff_date="2026-08-20",
            reviewer="reviewer",
        )
        store.lock_topic(topic_id, reviewer="reviewer")

    matrix = ReviewStore(database).list_coverage_matrix()
    for metric_code in ("uacr", "bone_density_t_score", "calcium"):
        row = next(row for row in matrix if row["metric_code"] == metric_code)
        assert row["coverage_status"] == "topic_locked"
