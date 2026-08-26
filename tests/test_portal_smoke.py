from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database
from genesis_evidence.portal.api import create_app

API_KEY = "test-evidence-key-0123456789"


def test_portal_app_builds_test_client_and_serves_health(tmp_path) -> None:
    database_path = tmp_path / "evidence.sqlite3"
    Database(database_path).initialize()
    app = create_app(
        database_path=database_path,
        evidence_api_key=API_KEY,
    )

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
