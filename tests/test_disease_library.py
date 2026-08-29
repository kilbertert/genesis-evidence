from __future__ import annotations

from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database, PaperStore, ReviewStore
from genesis_evidence.review.api import create_app
from genesis_evidence.review.service import EvidenceReviewService

from .test_review_workflow import _review_case

API_KEY = "test-review-key-with-32-characters"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def _admitted_review_case(
    database: Database, *, condition_code: str = "COND_VITAMIN_D_DEFICIENCY"
) -> tuple[str, str]:
    """Create an admitted paper under the given condition.

    ``_review_case`` builds a vitamin-D topic plus a paper. To admit under a
    different condition we must first re-point that paper's own topic to the
    target condition, because ``admit_paper`` requires the condition codes to
    match the paper's full-text-included topics.
    """
    paper_id, claim_id = _review_case(database)
    if condition_code != "COND_VITAMIN_D_DEFICIENCY":
        with database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT cp.paper_id, cr.topic_id
                FROM collection_papers cp
                JOIN collection_runs cr ON cr.id = cp.run_id
                WHERE cp.paper_id = ?
                """,
                (paper_id,),
            ).fetchall()
            topic_ids = {row["topic_id"] for row in rows if row["paper_id"] == paper_id}
            assert topic_ids, "expected _review_case to attach at least one topic"
            connection.execute(
                "UPDATE evidence_topics SET condition_code = ? WHERE id = ?",
                (condition_code, next(iter(topic_ids))),
            )
    service = EvidenceReviewService(ReviewStore(database), PaperStore(database))
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=[condition_code],
        differences_confirmed=True,
        study_design="cohort_study",
        publication_role="primary",
        identity_confirmed=True,
    )
    return paper_id, claim_id


def test_disease_library_requires_auth(tmp_path) -> None:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    client = TestClient(create_app(database_path=path, api_key=API_KEY, reviewer_id="reviewer"))

    assert client.get("/api/review/disease-papers").status_code == 401


def test_disease_library_lists_admitted_papers_per_condition(tmp_path) -> None:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    _admitted_review_case(database)
    client = TestClient(create_app(database_path=path, api_key=API_KEY, reviewer_id="reviewer"))

    response = client.get("/api/review/disease-papers", headers=HEADERS)

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 12  # all conditions present
    taken = next(row for row in rows if row["condition_code"] == "COND_VITAMIN_D_DEFICIENCY")
    assert taken["condition_name"] == "维生素 D 缺乏风险"
    assert taken["paper_count"] == 1
    assert taken["study_designs"] == {"cohort_study": 1}
    assert len(taken["papers"]) == 1
    paper = taken["papers"][0]
    assert paper["title"] == "Vitamin D and frailty"
    assert paper["study_design"] == "cohort_study"
    assert paper["integrity_status"] == "clear"
    assert paper["publication_status"] == "formal"
    # an un-taken condition has zero papers
    untouched = next(row for row in rows if row["condition_code"] == "COND_OSTEOPOROSIS_RISK")
    assert untouched["paper_count"] == 0
    assert untouched["papers"] == []
    assert untouched["study_designs"] == {}


def test_disease_library_separates_papers_by_condition(tmp_path) -> None:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    _admitted_review_case(database, condition_code="COND_VITAMIN_D_DEFICIENCY")
    _admitted_review_case(database, condition_code="COND_OSTEOPOROSIS_RISK")

    rows = ReviewStore(database).list_disease_papers()
    vitamin_d = next(row for row in rows if row["condition_code"] == "COND_VITAMIN_D_DEFICIENCY")
    osteoporosis = next(row for row in rows if row["condition_code"] == "COND_OSTEOPOROSIS_RISK")

    assert vitamin_d["paper_count"] == 1
    assert osteoporosis["paper_count"] == 1
    assert vitamin_d["papers"][0]["id"] != osteoporosis["papers"][0]["id"]


def test_disease_library_adopted_design_wins_over_candidate(tmp_path) -> None:
    """The reviewed study design written at admission is what the card reports.

    ``admit_paper`` writes the passed ``study_design`` onto the verified
    ``studies`` row, so a paper admitted as SR/MA reports SR/MA even though its
    ingestion-time candidate design (from ``_review_case``) was ``cohort_study``.
    """
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    paper_id, _ = _review_case(database)
    service = EvidenceReviewService(ReviewStore(database), PaperStore(database))
    service.admit_paper(
        paper_id,
        reviewer="reviewer-1",
        condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
        differences_confirmed=True,
        study_design="systematic_review_meta_analysis",
        publication_role="primary",
        identity_confirmed=True,
    )

    rows = ReviewStore(database).list_disease_papers()
    row = next(r for r in rows if r["condition_code"] == "COND_VITAMIN_D_DEFICIENCY")

    assert row["paper_count"] == 1
    assert row["papers"][0]["study_design"] == "systematic_review_meta_analysis"
    assert row["study_designs"] == {"systematic_review_meta_analysis": 1}