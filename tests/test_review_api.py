from __future__ import annotations

from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database
from genesis_evidence.review.api import _review_api_key, create_app

from .test_review_workflow import _review_case

API_KEY = "test-review-key-with-32-characters"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
REVIEWER = "authenticated-reviewer-1"


def test_review_api_requires_key_and_serves_workbench(tmp_path) -> None:
    client = TestClient(
        create_app(
            database_path=tmp_path / "evidence.sqlite3",
            api_key=API_KEY,
            reviewer_id=REVIEWER,
        )
    )
    assert client.get("/health").json() == {"status": "ok"}
    page = client.get("/")
    assert "论文证据审核工作台" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/review/papers").status_code == 401
    assert client.get("/api/review/papers").headers["www-authenticate"] == "Bearer"
    assert client.get("/api/review/papers", headers=HEADERS).status_code == 200
    assert client.get("/api/review/me", headers=HEADERS).json() == {"reviewer_id": REVIEWER}


def test_review_api_completes_admission_claim_and_card_flow(tmp_path) -> None:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    paper_id, claim_id = _review_case(database)
    client = TestClient(create_app(database_path=path, api_key=API_KEY, reviewer_id=REVIEWER))
    from .test_review_workflow import _complete_topic

    topic_id = _complete_topic(database, paper_id)

    queue = client.get("/api/review/papers", headers=HEADERS).json()
    assert [item["id"] for item in queue] == [paper_id]
    assert (
        client.post(
            f"/api/review/papers/{paper_id}/admit",
            headers=HEADERS,
            json={
                "condition_codes": ["COND_VITAMIN_D_DEFICIENCY"],
            },
        ).json()["status"]
        == "internally_admitted"
    )
    assert (
        client.post(
            f"/api/review/claims/{claim_id}",
            headers=HEADERS,
            json={
                "decision": "approved",
                "corrected_text": "Lower vitamin D status was associated with frailty.",
                "corrected_study_design": "cohort_study",
                "inference": "associational",
                "risk_of_bias": {
                    "tool": "exposure_study",
                    "overall": "some_concerns",
                    "rationale": "Residual confounding remains possible.",
                },
                "applicability": "Applies to older adults with measured serum 25(OH)D.",
                "condition_code": "COND_VITAMIN_D_DEFICIENCY",
            },
        ).status_code
        == 200
    )
    card = client.post(
        "/api/review/cards",
        headers=HEADERS,
        json={
            "topic_id": topic_id,
            "condition_code": "COND_VITAMIN_D_DEFICIENCY",
            "version": "1.0.0",
            "claim_ids": [claim_id],
            "patient_body": "维生素 D 状态与衰弱之间存在研究关联。",
            "profile": {
                "certainty": "moderate",
                "certainty_rationale": "The complete eligible evidence body was reviewed.",
                "estimate_target": "Association between vitamin D status and frailty",
                "interpretations": {claim_id: "supports"},
            },
        },
    ).json()
    for target in ("in_review", "approved", "published"):
        response = client.post(
            f"/api/review/cards/{card['id']}/transition",
            headers=HEADERS,
            json={"target": target},
        )
        assert response.status_code == 200
    cards = client.get("/api/review/cards", headers=HEADERS).json()
    assert cards[0]["status"] == "published"
    assert cards[0]["claim_ids"] == [claim_id]
    assert cards[0]["paper_ids"] == [paper_id]
    assert cards[0]["evidence_profile_id"]
    with database.connect() as connection:
        assert connection.execute("SELECT reviewer FROM claim_reviews").fetchone()[0] == REVIEWER
        assert connection.execute("SELECT reviewer FROM knowledge_cards").fetchone()[0] == REVIEWER


def test_review_api_rejects_weak_server_key(tmp_path) -> None:
    try:
        create_app(
            database_path=tmp_path / "evidence.sqlite3",
            api_key="short",
            reviewer_id=REVIEWER,
        )
    except ValueError as exc:
        assert "24 characters" in str(exc)
    else:
        raise AssertionError("weak review key was accepted")


def test_request_body_cannot_spoof_authenticated_reviewer(tmp_path) -> None:
    client = TestClient(
        create_app(
            database_path=tmp_path / "evidence.sqlite3",
            api_key=API_KEY,
            reviewer_id=REVIEWER,
        )
    )
    response = client.post(
        "/api/review/topics",
        headers=HEADERS,
        json={
            "reviewer": "spoofed-reviewer",
            "code": "vitamin-d-frailty",
            "version": "1",
            "condition_code": "COND_VITAMIN_D_DEFICIENCY",
            "review_question": "Question",
            "picots": {
                "population": "Older adults",
                "intervention_or_exposure": "Vitamin D",
                "comparator": "Lower exposure",
                "outcomes": "Frailty",
                "timing": "Baseline",
                "setting": "Any",
            },
            "eligible_study_designs": ["cohort_study"],
            "inclusion_criteria": ["Human"],
            "exclusion_reasons": ["wrong_population"],
            "required_search_streams": ["effect"],
            "evidence_cutoff_date": "2026-08-12",
        },
    )
    assert response.status_code == 422


def test_new_review_key_takes_precedence_over_legacy_compatibility(monkeypatch) -> None:
    monkeypatch.setenv("GENESIS_REVIEW_API_KEY", "legacy-review-key-with-32-characters")
    monkeypatch.setenv("GENESIS_EVIDENCE_REVIEW_API_KEY", "new-review-key-with-32-characters")
    assert _review_api_key() == "new-review-key-with-32-characters"
    monkeypatch.delenv("GENESIS_EVIDENCE_REVIEW_API_KEY")
    assert _review_api_key() == "legacy-review-key-with-32-characters"


def test_review_api_lists_and_retries_failed_extraction_jobs(tmp_path) -> None:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, created_at) VALUES ('paper-1', 'Queued paper', 'now')"
        )
        connection.execute(
            """
            INSERT INTO paper_extraction_jobs(
                id, paper_id, status, stage, error_class, error_message,
                created_at, updated_at
            ) VALUES ('job-1', 'paper-1', 'failed', 'extraction_b',
                'PaperAnalysisError', 'provider failed', 'now', 'now')
            """
        )
    client = TestClient(create_app(database_path=path, api_key=API_KEY, reviewer_id=REVIEWER))

    jobs = client.get("/api/review/extraction-jobs", headers=HEADERS).json()
    assert jobs[0]["status"] == "failed"
    assert "extraction_json" not in jobs[0]
    response = client.post("/api/review/extraction-jobs/job-1/retry", headers=HEADERS)
    assert response.json() == {"id": "job-1", "status": "queued"}


def test_workbench_does_not_present_pending_extraction_as_missing_results(tmp_path) -> None:
    client = TestClient(
        create_app(
            database_path=tmp_path / "evidence.sqlite3",
            api_key=API_KEY,
            reviewer_id=REVIEWER,
        )
    )
    page = client.get("/").text
    assert "任务完成后才会显示通读摘要、独立抽取和差异检查" in page
