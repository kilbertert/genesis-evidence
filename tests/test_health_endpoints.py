import pytest
from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database
from genesis_evidence.portal.api import create_app as create_portal_app
from genesis_evidence.review.api import create_app as create_review_app


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    Database(path).initialize()
    return path


def test_portal_health_endpoint_smoke(database_path) -> None:
    client = TestClient(
        create_portal_app(
            database_path=database_path,
            evidence_api_key="test-evidence-key-0123456789",
        )
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_review_health_endpoint_smoke(database_path) -> None:
    client = TestClient(
        create_review_app(
            database_path=database_path,
            api_key="test-review-key-with-32-characters",
            reviewer_id="smoke-reviewer",
        )
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
