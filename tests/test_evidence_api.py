from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database
from genesis_evidence.integrations.health_flow import build_evidence_request
from genesis_evidence.portal.api import create_app

API_KEY = "test-evidence-key-0123456789"


def _client(tmp_path, *, api_key: str = API_KEY) -> tuple[Database, TestClient]:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    app = create_app(
        database_path=path,
        object_path=tmp_path / "objects",
        evidence_api_key=api_key,
    )
    return database, TestClient(
        app,
        headers={"X-Genesis-Evidence-Key": api_key},
    )


def _publish_prediabetes_card(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO evidence_topics(
                id, code, version, condition_code, status, review_question, picots_json,
                eligible_study_designs_json, inclusion_criteria_json, exclusion_reasons_json,
                required_search_streams_json, evidence_cutoff_date, created_by, created_at,
                locked_by, locked_at
            ) VALUES ('topic-1', 'test-topic', '1', 'COND_PREDIABETES', 'locked',
                'Test question', '{}', '[]', '[]', '[]', '[]', '2026-08-11',
                'reviewer', '2026-08-11T00:00:00Z', 'reviewer', '2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO evidence_profiles(
                id, topic_id, condition_code, version, ingredient_name, ingredient_form,
                population, baseline_nutrient_status, dose, comparator, outcome, timepoint,
                estimate_target, evidence_body_complete, certainty, certainty_rationale,
                evidence_cutoff_date, reviewer, reviewed_at, created_at
            ) VALUES ('profile-1', 'topic-1', 'COND_PREDIABETES', '1.0.0', 'Test ingredient',
                'Test form', 'Adults 40+', 'Not reported', 'Test dose', 'Comparator',
                'Outcome', 'Timepoint', 'Target', 1, 'moderate', 'Test-only profile',
                '2026-08-11', 'reviewer', '2026-08-11T00:00:00Z', '2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO papers(id, title, abstract, doi, created_at)
            VALUES ('paper-1', 'Test paper', 'Test abstract', '10.1000/test-paper',
                '2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO paper_extractions(
                id, paper_id, model, extraction_run_id, extraction_json,
                second_model, second_run_id, second_extraction_json,
                check_model, check_run_id, consistency_status, consistency_json, created_at
            ) VALUES ('extraction-1', 'paper-1', 'test-model', 'run-a', '{}',
                'test-model-b', 'run-b', '{}', 'check-model', 'check-run',
                'consistent', '{}', '2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO claims(
                id, paper_id, extraction_id, candidate_text, evidence_text, locator,
                candidate_study_design, status, created_at
            ) VALUES ('claim-1', 'paper-1', 'extraction-1', 'Test claim', 'Test evidence',
                'p. 4', 'randomized_controlled_trial', 'reviewed',
                '2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO claim_reviews(
                claim_id, decision, corrected_text, corrected_study_design, inference,
                risk_of_bias_json, applicability, condition_code, reviewer, reviewed_at
            ) VALUES ('claim-1', 'approved', 'Test claim', 'randomized_controlled_trial',
                'causal', '{}', 'Adults 40+', 'COND_PREDIABETES', 'reviewer',
                '2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_cards(
                id, condition_code, version, status, grade, evidence_profile_id, reviewer,
                reviewed_at, published_at, patient_visible_body, created_at
            ) VALUES ('card-1', 'COND_PREDIABETES', '1.0.0', 'published', 'moderate',
                'profile-1', 'reviewer', '2026-08-11T00:00:00Z', '2026-08-11T00:00:00Z',
                '这是经过审核的营养健康知识。', '2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO card_claims(card_id, claim_id, evidence_text, locator)
            VALUES ('card-1', 'claim-1', 'Test evidence', 'p. 4')
            """
        )


def _observation(**overrides):
    value = {
        "observation_id": "metric-1",
        "confirmation_status": "confirmed",
        "metric_code": "fasting_glucose",
        "value": 6.8,
        "unit": "mmol/L",
        "reference_low": 3.9,
        "reference_high": 6.1,
        "evidence_text": "空腹血糖 6.8 mmol/L 3.9-6.1 H",
        "source_file_index": 1,
        "source_page": 2,
        "source_id": "report-1/page-2",
        "bbox_normalized": [10, 20, 100, 120],
    }
    value.update(overrides)
    return value


def test_evidence_api_returns_only_published_cards_and_audit(tmp_path) -> None:
    database, client = _client(tmp_path)
    _publish_prediabetes_card(database)
    response = client.post(
        "/api/evidence/matches",
        headers={"X-Correlation-Id": "00000000-0000-4000-8000-000000000001"},
        json={
            "schema_version": "1",
            "observations": [
                _observation(),
                _observation(
                    observation_id="metric-normal",
                    value=5.2,
                    evidence_text="空腹血糖 5.2 mmol/L 3.9-6.1 N",
                ),
                _observation(
                    observation_id="metric-unmatched",
                    metric_code="uric_acid",
                    value=500,
                    unit="umol/L",
                    reference_low=200,
                    reference_high=420,
                    evidence_text="尿酸 500 umol/L 200-420 H",
                ),
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correlation_id"] == "00000000-0000-4000-8000-000000000001"
    assert body["sorting_version"] == "published-card-reference-range-v1"
    assert body["findings"][0]["condition_code"] == "COND_PREDIABETES"
    assert body["findings"][0]["source_observation_ids"] == ["metric-1"]
    assert body["findings"][0]["card"]["status"] == "published"
    assert body["findings"][0]["card"]["id"] == "card-1"
    assert body["findings"][0]["source_observations"] == [
        {
            "observation_id": "metric-1",
            "metric_code": "fasting_glucose",
            "value": 6.8,
            "unit": "mmol/L",
            "reference_low": 3.9,
            "reference_high": 6.1,
            "evidence_text": "空腹血糖 6.8 mmol/L 3.9-6.1 H",
            "source_file_index": 1,
            "source_page": 2,
            "source_id": "report-1/page-2",
            "bbox_normalized": [10, 20, 100, 120],
        }
    ]
    assert body["patient_reply"] == {
        "title": "体检报告解读与健康风险提示",
        "summary": "根据已确认的报告指标，发现 1 个有正式知识卡支持的健康问题。",
        "findings": [
            {
                "condition_code": "COND_PREDIABETES",
                "condition_name": "糖尿病前期 / 糖代谢异常",
                "urgency": "routine",
                "evidence_strength": "moderate",
                "needs_recheck": True,
                "department": "内分泌科",
                "recheck_direction": "复查空腹血糖与糖化血红蛋白",
                "card_id": "card-1",
                "card_version": "1.0.0",
                "patient_visible_body": "这是经过审核的营养健康知识。",
                "source_observation_ids": ["metric-1"],
                "source_observations": [
                    {
                        "observation_id": "metric-1",
                        "metric_code": "fasting_glucose",
                        "value": 6.8,
                        "unit": "mmol/L",
                        "reference_low": 3.9,
                        "reference_high": 6.1,
                        "evidence_text": "空腹血糖 6.8 mmol/L 3.9-6.1 H",
                        "source_file_index": 1,
                        "source_page": 2,
                        "source_id": "report-1/page-2",
                        "bbox_normalized": [10, 20, 100, 120],
                    }
                ],
            }
        ],
        "unmatched_count": 1,
        "disclaimer": "本提示仅基于已确认指标和已发布知识卡，不构成诊断或治疗建议。",
    }
    assert body["findings"][0]["sorting"] == {
        "urgency": "routine",
        "abnormality_severity": 1,
        "evidence_strength": "moderate",
        "needs_recheck": True,
        "department": "内分泌科",
        "epidemiology_background": "",
    }
    assert body["unmatched"] == [
        {
            "observation_id": "metric-unmatched",
            "condition_codes": ["COND_HYPERURICEMIA_RISK"],
            "reason": "no_published_knowledge_card",
        }
    ]
    assert {item["reason"] for item in body["skipped"]} == {"within_reference_range"}
    with database.connect() as connection:
        audit = connection.execute(
            "SELECT entity_id, action, actor, detail_json FROM audit_events"
        ).fetchone()
    assert audit["entity_id"] == "00000000-0000-4000-8000-000000000001"
    assert audit["action"] == "published_card_match"
    assert audit["actor"] == "health-flow"


def test_evidence_api_requires_key_and_confirmed_status(tmp_path) -> None:
    _, client = _client(tmp_path, api_key="secret-key-012345678901234")
    payload = {"schema_version": "1", "observations": [_observation()]}
    unauthenticated = TestClient(client.app)
    assert unauthenticated.post("/api/evidence/matches", json=payload).status_code == 401
    assert (
        client.post(
            "/api/evidence/matches",
            json={
                "schema_version": "1",
                "observations": [_observation(confirmation_status="pending")],
            },
        ).status_code
        == 422
    )


def test_evidence_api_rejects_non_opaque_correlation_id(tmp_path) -> None:
    _, client = _client(tmp_path)
    response = client.post(
        "/api/evidence/matches",
        headers={"X-Correlation-Id": "patient-name"},
        json={"schema_version": "1", "observations": []},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "correlation ID must be a UUID"


def test_evidence_api_rejects_missing_source_number(tmp_path) -> None:
    _, client = _client(tmp_path)
    response = client.post(
        "/api/evidence/matches",
        json={
            "schema_version": "1",
            "observations": [_observation(evidence_text="空腹血糖 6.8 mmol/L")],
        },
    )
    assert response.status_code == 400
    assert "source evidence" in response.json()["detail"]


def test_health_flow_adapter_payload_reaches_published_card_match(tmp_path) -> None:
    database, client = _client(tmp_path)
    _publish_prediabetes_card(database)
    adapted = build_evidence_request(
        [
            {
                "metric_name": "空腹血糖",
                "metric_value": "6.8",
                "unit": "mmol/L",
                "reference_range": "3.9-6.1",
                "page_number": 2,
                "source_file_index": 1,
                "source_id": "health-flow/report-1/page-2",
                "evidence_text": "空腹血糖 6.8 mmol/L 参考范围 3.9-6.1 H",
            }
        ],
        confirmed=True,
    )

    response = client.post(
        "/api/evidence/matches",
        json=adapted.request.model_dump(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["findings"][0]["condition_code"] == "COND_PREDIABETES"
    assert body["findings"][0]["source_observations"][0]["source_page"] == 2


def test_evidence_api_exposes_versioned_response_schema(tmp_path) -> None:
    _, client = _client(tmp_path)
    schema = client.get("/openapi.json").json()
    response = schema["paths"]["/api/evidence/matches"]["post"]["responses"]["200"]

    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EvidenceMatchResponse"
    }
