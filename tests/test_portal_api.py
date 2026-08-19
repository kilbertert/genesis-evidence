import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database
from genesis_evidence.portal.api import create_app

API_KEY = "test-evidence-key-0123456789"


def _client(tmp_path) -> TestClient:
    database_path = tmp_path / "evidence.sqlite3"
    Database(database_path).initialize()
    app = create_app(
        database_path=database_path,
        evidence_api_key=API_KEY,
    )
    return TestClient(app, headers={"X-Genesis-Evidence-Key": API_KEY})


def test_portal_is_reduced_to_authenticated_evidence_api(tmp_path) -> None:
    client = _client(tmp_path)

    root = client.get("/")
    assert root.json() == {
        "service": "genesis-evidence-api",
        "scope": "published-evidence-read-only",
    }
    assert root.headers["cache-control"] == "no-store"
    assert client.get("/health").json() == {"status": "ok"}
    assert {"code": "fasting_glucose", "label": "空腹血糖"} in client.get("/api/metrics").json()
    assert client.get("/openapi.json").status_code == 404


def test_evidence_endpoints_require_key_and_legacy_reports_are_gone(tmp_path) -> None:
    client = _client(tmp_path)
    unauthenticated = TestClient(client.app)

    assert unauthenticated.get("/api/metrics").status_code == 401
    assert (
        unauthenticated.post(
            "/api/evidence/matches",
            json={"schema_version": "2", "observations": []},
        ).status_code
        == 401
    )
    assert client.post("/api/reports").status_code == 404
    assert client.get("/api/reports/not-a-report").status_code == 404


def test_evidence_api_refuses_to_start_without_key(tmp_path) -> None:
    with pytest.raises(ValueError, match="GENESIS_EVIDENCE_API_KEY is required"):
        create_app(
            database_path=tmp_path / "evidence.sqlite3",
            evidence_api_key="",
        )

    with pytest.raises(ValueError, match="at least 24 characters"):
        create_app(
            database_path=tmp_path / "short.sqlite3",
            evidence_api_key="too-short",
        )


def test_evidence_api_requires_an_initialized_database(tmp_path) -> None:
    with pytest.raises(ValueError, match="database must be initialized"):
        create_app(
            database_path=tmp_path / "missing.sqlite3",
            evidence_api_key=API_KEY,
        )


def test_evidence_api_import_does_not_load_legacy_report_store() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import genesis_evidence.portal.api; "
            "assert 'genesis_evidence.core.store.reports' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
