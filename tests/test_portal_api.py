from __future__ import annotations

from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database
from genesis_evidence.portal.api import create_app
from genesis_evidence.reports.extraction import (
    HealthReportExtractor,
    ModelObservation,
    ModelReportExtraction,
    ReportProviderResult,
)


class FakeProvider:
    def __init__(self, *, ambiguous: bool = False, subject: str = "same") -> None:
        self.ambiguous = ambiguous
        self.subject = subject
        self.filenames: tuple[str, ...] = ()

    async def understand(self, files):
        self.filenames = tuple(item.filename for item in files)
        return ReportProviderResult(
            provider="fake",
            model="fake-model",
            run_id="fake-run",
            extraction=ModelReportExtraction(
                subject_consistency=self.subject,
                inferred_age=65,
                inferred_sex="female",
                observations=[
                    ModelObservation(
                        source_file_index=1,
                        source_page=1,
                        name="空腹血糖",
                        value=6.8,
                        unit="mmol/L",
                        reference_low=3.9,
                        reference_high=6.1,
                        flag="high",
                        evidence="空腹血糖 6.8 mmol/L 3.9-6.1 H",
                        extraction_status="ambiguous" if self.ambiguous else "clear",
                    )
                ],
                abnormality_audit=[],
            ),
        )


def _client(tmp_path, provider: FakeProvider | None = None):
    path = tmp_path / "evidence.sqlite3"
    provider = provider or FakeProvider()
    app = create_app(
        database_path=path,
        object_path=tmp_path / "objects",
        extractor=HealthReportExtractor(max_bytes=1024, provider=provider),
        max_file_bytes=1024,
    )
    return path, provider, TestClient(app)


def _upload(client: TestClient):
    return client.post(
        "/api/reports",
        files=[
            ("files", ("page-1.txt", b"first", "text/plain")),
            ("files", ("page-2.txt", b"second", "text/plain")),
        ],
    )


def _publish_card(path, condition_code: str = "COND_PREDIABETES") -> None:
    with Database(path).transaction() as connection:
        connection.execute(
            """
            INSERT INTO evidence_topics(
                id, code, version, condition_code, status, review_question, picots_json,
                eligible_study_designs_json, inclusion_criteria_json, exclusion_reasons_json,
                required_search_streams_json, evidence_cutoff_date, created_by, created_at,
                locked_by, locked_at
            ) VALUES ('topic-1', 'test-topic', '1', ?, 'locked', 'Test question', '{}',
                '[]', '[]', '[]', '[]', '2026-08-11', 'reviewer',
                '2026-08-11T00:00:00Z', 'reviewer', '2026-08-11T00:00:00Z')
            """,
            (condition_code,),
        )
        connection.execute(
            """
            INSERT INTO evidence_profiles(
                id, topic_id, condition_code, version, ingredient_name, ingredient_form,
                population, baseline_nutrient_status, dose, comparator, outcome,
                timepoint, estimate_target, evidence_body_complete, certainty,
                certainty_rationale, evidence_cutoff_date, reviewer, reviewed_at, created_at
            ) VALUES ('profile-1', 'topic-1', ?, '1.0.0', 'Test ingredient', 'Test form',
                'Adults 40+', 'Not reported', 'Test dose', 'Comparator', 'Outcome',
                'Timepoint', 'Test target', 1, 'moderate', 'Test-only reviewed profile',
                '2026-08-11', 'reviewer', '2026-08-11T00:00:00Z',
                '2026-08-11T00:00:00Z')
            """,
            (condition_code,),
        )
        connection.execute(
            """
            INSERT INTO knowledge_cards(
                id, condition_code, version, status, grade, evidence_profile_id,
                reviewer, reviewed_at,
                published_at, patient_visible_body, created_at
            ) VALUES ('card-1', ?, '1.0.0', 'published', 'moderate', 'profile-1', 'reviewer',
                '2026-08-11T00:00:00Z', '2026-08-11T00:00:00Z',
                '这是经过审核的营养健康知识。', '2026-08-11T00:00:00Z')
            """,
            (condition_code,),
        )


def test_portal_serves_named_no_store_page_and_metric_catalog(tmp_path) -> None:
    _, _, client = _client(tmp_path)
    page = client.get("/")
    assert "体检报告解读与健康风险提示" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert client.get("/health").json() == {"status": "ok"}
    metrics = client.get("/api/metrics").json()
    assert {"code": "fasting_glucose", "label": "空腹血糖"} in metrics


def test_upload_preserves_file_order_and_returns_pending_confirmation(tmp_path) -> None:
    _, provider, client = _client(tmp_path)
    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert provider.filenames == ("page-1.txt", "page-2.txt")
    assert body["status"] == "pending_confirmation"
    assert body["files"][0]["original_name"] == "page-1.txt"
    assert body["files"][1]["original_name"] == "page-2.txt"
    assert body["observations"][0]["evidence_text"].startswith("空腹血糖 6.8")
    assert body["access_token"]


def test_ambiguous_row_is_visible_and_defaults_to_excluded(tmp_path) -> None:
    _, _, client = _client(tmp_path, FakeProvider(ambiguous=True))
    observation = _upload(client).json()["observations"][0]
    assert observation["extraction_status"] == "ambiguous"
    assert observation["default_decision"] == "excluded"
    assert "模型标记为待核对" in observation["validation_issues"]


def test_uncertain_subject_warning_survives_persistence(tmp_path) -> None:
    _, _, client = _client(tmp_path, FakeProvider(subject="uncertain"))
    body = _upload(client).json()
    assert body["subject_consistency"] == "uncertain"
    assert any("无法确认所有页面" in warning for warning in body["warnings"])


def test_confirmation_and_assessment_return_only_published_card_content(tmp_path) -> None:
    path, _, client = _client(tmp_path)
    uploaded = _upload(client).json()
    token = uploaded["access_token"]
    headers = {"X-Report-Token": token}
    observation_id = uploaded["observations"][0]["id"]
    _publish_card(path)

    confirmed = client.post(
        f"/api/reports/{uploaded['report_id']}/confirm",
        headers=headers,
        json={
            "observations": [
                {
                    "observation_id": observation_id,
                    "decision": "confirmed",
                    "metric_code": "fasting_glucose",
                }
            ]
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assessed = client.post(
        f"/api/reports/{uploaded['report_id']}/assess",
        headers=headers,
    )
    assert assessed.status_code == 200
    finding = assessed.json()["findings"][0]
    assert finding["card_id"] == "card-1"
    assert finding["patient_visible_body"] == "这是经过审核的营养健康知识。"
    assert finding["source_observation_ids"] == [observation_id]


def test_report_endpoints_do_not_accept_missing_or_wrong_token(tmp_path) -> None:
    _, _, client = _client(tmp_path)
    uploaded = _upload(client).json()
    path = f"/api/reports/{uploaded['report_id']}"
    assert client.get(path).status_code == 404
    assert client.get(path, headers={"X-Report-Token": "wrong"}).status_code == 404


def test_public_upload_endpoint_has_a_global_model_budget(tmp_path) -> None:
    path = tmp_path / "evidence.sqlite3"
    app = create_app(
        database_path=path,
        object_path=tmp_path / "objects",
        extractor=HealthReportExtractor(max_bytes=1024, provider=FakeProvider()),
        max_file_bytes=1024,
        upload_limit=1,
    )
    client = TestClient(app)
    assert _upload(client).status_code == 200
    limited = _upload(client)
    assert limited.status_code == 429
    assert "请稍后再试" in limited.json()["detail"]
