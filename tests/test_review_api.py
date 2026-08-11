from __future__ import annotations

from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database
from genesis_evidence.review.api import create_app

from .test_review_workflow import _review_case

API_KEY = "test-review-key-with-32-characters"
HEADERS = {"X-Review-Key": API_KEY}


def test_review_api_requires_key_and_serves_workbench(tmp_path) -> None:
    client = TestClient(
        create_app(database_path=tmp_path / "evidence.sqlite3", api_key=API_KEY)
    )
    assert client.get("/health").json() == {"status": "ok"}
    page = client.get("/")
    assert "论文证据审核工作台" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/review/papers").status_code == 401
    assert client.get("/api/review/papers", headers=HEADERS).status_code == 200


def test_review_api_completes_admission_claim_and_card_flow(tmp_path) -> None:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    paper_id, claim_id = _review_case(database)
    client = TestClient(create_app(database_path=path, api_key=API_KEY))

    queue = client.get("/api/review/papers", headers=HEADERS).json()
    assert [item["id"] for item in queue] == [paper_id]
    assert client.post(
        f"/api/review/papers/{paper_id}/admit",
        headers=HEADERS,
        json={
            "reviewer": "reviewer-1",
            "condition_codes": ["COND_VITAMIN_D_DEFICIENCY"],
        },
    ).json()["status"] == "internally_admitted"
    assert client.post(
        f"/api/review/claims/{claim_id}",
        headers=HEADERS,
        json={
            "reviewer": "reviewer-1",
            "decision": "approved",
            "corrected_text": "Lower vitamin D status was associated with frailty.",
            "corrected_study_design": "cohort_study",
            "inference": "associational",
            "grade": "low",
            "condition_code": "COND_VITAMIN_D_DEFICIENCY",
        },
    ).status_code == 200
    card = client.post(
        "/api/review/cards",
        headers=HEADERS,
        json={
            "condition_code": "COND_VITAMIN_D_DEFICIENCY",
            "version": "1.0.0",
            "claim_ids": [claim_id],
            "reviewer": "reviewer-1",
            "patient_body": "维生素 D 状态与衰弱之间存在研究关联。",
        },
    ).json()
    for target in ("in_review", "approved", "published"):
        response = client.post(
            f"/api/review/cards/{card['id']}/transition",
            headers=HEADERS,
            json={"reviewer": "reviewer-1", "target": target},
        )
        assert response.status_code == 200
    cards = client.get("/api/review/cards", headers=HEADERS).json()
    assert cards[0]["status"] == "published"
    assert cards[0]["claim_ids"] == [claim_id]
    assert cards[0]["paper_ids"] == [paper_id]


def test_review_api_rejects_weak_server_key(tmp_path) -> None:
    try:
        create_app(database_path=tmp_path / "evidence.sqlite3", api_key="short")
    except ValueError as exc:
        assert "24 characters" in str(exc)
    else:
        raise AssertionError("weak review key was accepted")
