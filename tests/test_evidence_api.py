from fastapi.testclient import TestClient

from genesis_evidence.core.contracts import EvidenceMatchResponse
from genesis_evidence.core.store import Database
from genesis_evidence.integrations.health_flow import build_evidence_request
from genesis_evidence.portal.api import create_app

API_KEY = "test-evidence-key-0123456789"


def _client(tmp_path, *, api_key: str = API_KEY) -> tuple[Database, TestClient]:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    app = create_app(
        database_path=path,
        evidence_api_key=api_key,
    )
    return database, TestClient(
        app,
        headers={"X-Genesis-Evidence-Key": api_key},
    )


def _publish_scoped_card(
    database: Database,
    *,
    condition_code: str = "COND_PREDIABETES",
    scope_key: str = "metric:fasting_glucose",
    card_id: str = "card-1",
    profile_id: str = "profile-1",
    topic_id: str = "topic-1",
    claim_id: str = "claim-1",
    paper_id: str = "paper-1",
    grade: str = "moderate",
    version: str = "1.0.0",
    doi: str = "10.1000/test-paper",
) -> None:
    extraction_id = f"extraction-{paper_id}"
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO evidence_topics(
                id, code, version, condition_code, status, review_question, picots_json,
                eligible_study_designs_json, inclusion_criteria_json, exclusion_reasons_json,
                required_search_streams_json, evidence_cutoff_date, created_by, created_at,
                locked_by, locked_at
            ) VALUES (?, ?, '1', ?, 'locked',
                'Test question', '{}', '[]', '[]', '[]', '[]', '2026-08-11',
                'reviewer', '2026-08-11T00:00:00Z', 'reviewer', '2026-08-11T00:00:00Z')
            """,
            (topic_id, f"test-topic-{topic_id}", condition_code),
        )
        connection.execute(
            """
            INSERT INTO evidence_profiles(
                id, topic_id, condition_code, scope_key, version, ingredient_name, ingredient_form,
                population, baseline_nutrient_status, dose, comparator, outcome, timepoint,
                estimate_target, evidence_body_complete, certainty, certainty_rationale,
                evidence_cutoff_date, reviewer, reviewed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, 'Test ingredient',
                'Test form', 'Adults 40+', 'Not reported', 'Test dose', 'Comparator',
                'Outcome', 'Timepoint', 'Target', 1, 'moderate', 'Test-only profile',
                '2026-08-11', 'reviewer', '2026-08-11T00:00:00Z', '2026-08-11T00:00:00Z')
            """,
            (profile_id, topic_id, condition_code, scope_key, version),
        )
        connection.execute(
            """
            INSERT INTO papers(id, title, abstract, doi, created_at)
            VALUES (?, 'Test paper', 'Test abstract', ?,
                '2026-08-11T00:00:00Z')
            """,
            (paper_id, doi),
        )
        connection.execute(
            """
            INSERT INTO paper_extractions(
                id, paper_id, model, extraction_run_id, extraction_json,
                second_model, second_run_id, second_extraction_json,
                check_model, check_run_id, consistency_status, consistency_json, created_at
            ) VALUES (?, ?, 'test-model', 'run-a', '{}',
                'test-model-b', 'run-b', '{}', 'check-model', 'check-run',
                'consistent', '{}', '2026-08-11T00:00:00Z')
            """,
            (extraction_id, paper_id),
        )
        connection.execute(
            """
            INSERT INTO claims(
                id, paper_id, extraction_id, candidate_text, evidence_text, locator,
                candidate_study_design, status, created_at
            ) VALUES (?, ?, ?, 'Test claim', 'Test evidence',
                'p. 4', 'randomized_controlled_trial', 'reviewed',
                '2026-08-11T00:00:00Z')
            """,
            (claim_id, paper_id, extraction_id),
        )
        connection.execute(
            """
            INSERT INTO claim_reviews(
                claim_id, decision, corrected_text, corrected_study_design, inference,
                risk_of_bias_json, applicability, condition_code, reviewer, reviewed_at
            ) VALUES (?, 'approved', 'Test claim', 'randomized_controlled_trial',
                'causal', '{}', 'Adults 40+', ?, 'reviewer',
                '2026-08-11T00:00:00Z')
            """,
            (claim_id, condition_code),
        )
        connection.execute(
            """
            INSERT INTO knowledge_cards(
                id, condition_code, version, status, grade, evidence_profile_id, reviewer,
                reviewed_at, published_at, patient_visible_body, created_at
            ) VALUES (?, ?, ?, 'published', ?,
                ?, 'reviewer', '2026-08-11T00:00:00Z', '2026-08-11T00:00:00Z',
                '这是经过审核的营养健康知识。', '2026-08-11T00:00:00Z')
            """,
            (card_id, condition_code, version, grade, profile_id),
        )
        connection.execute(
            """
            INSERT INTO card_claims(card_id, claim_id, evidence_text, locator)
            VALUES (?, ?, 'Test evidence', 'p. 4')
            """,
            (card_id, claim_id),
        )


def _publish_prediabetes_card(database: Database) -> None:
    _publish_scoped_card(database)


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
            "schema_version": "2",
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
    assert body["findings"][0]["card"]["scope_key"] == "metric:fasting_glucose"
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
                "abnormality_severity": 1,
                "evidence_strength": "moderate",
                "needs_recheck": True,
                "department": "内分泌科",
                "recheck_direction": "复查空腹血糖与糖化血红蛋白",
                "card_id": "card-1",
                "card_version": "1.0.0",
                "evidence_profile_id": "profile-1",
                "patient_visible_body": "这是经过审核的营养健康知识。",
                "sources": [
                    {
                        "claim_id": "claim-1",
                        "paper_id": "paper-1",
                        "paper_title": "Test paper",
                        "doi": "10.1000/test-paper",
                        "evidence": "Test evidence",
                        "locator": "p. 4",
                    }
                ],
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
                "content_layer": "context_only",
                "action_status": "not_available",
                "action_message": (
                    "证据确定性已达到行动建议门槛，但当前知识卡尚未包含经审核的具体行动内容。"
                ),
                "product_status": "not_implemented",
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
            "metric_code": "uric_acid",
            "metric_label": "尿酸",
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


def test_metric_scope_prevents_cross_outcome_card_match(tmp_path) -> None:
    database, client = _client(tmp_path)
    _publish_scoped_card(
        database,
        condition_code="COND_DYSLIPIDEMIA",
        scope_key="metric:ldl_c",
        card_id="card-ldl",
        profile_id="profile-ldl",
        topic_id="topic-ldl",
        claim_id="claim-ldl",
        paper_id="paper-ldl",
    )
    triglycerides = _observation(
        metric_code="triglycerides",
        value=2.4,
        unit="mmol/L",
        reference_low=0.3,
        reference_high=1.7,
        evidence_text="甘油三酯 2.4 mmol/L 0.3-1.7 H",
        observation_id="metric-triglycerides",
        source_page=1,
    )
    response = client.post(
        "/api/evidence/matches",
        json={"schema_version": "2", "observations": [triglycerides]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["findings"] == []
    assert body["unmatched"] == [
        {
            "observation_id": "metric-triglycerides",
            "metric_code": "triglycerides",
            "metric_label": "甘油三酯",
            "condition_codes": ["COND_DYSLIPIDEMIA", "COND_MASLD_RISK"],
            "reason": "no_published_knowledge_card",
        }
    ]
    ldl = _observation(
        metric_code="ldl_c",
        value=4.2,
        reference_low=0,
        reference_high=3.4,
        evidence_text="低密度脂蛋白胆固醇 4.2 mmol/L 0-3.4 H",
        observation_id="metric-ldl",
        source_page=1,
    )
    response = client.post(
        "/api/evidence/matches",
        json={"schema_version": "2", "observations": [ldl]},
    )
    assert response.status_code == 200
    assert response.json()["findings"][0]["card"]["scope_key"] == "metric:ldl_c"


def test_non_hdl_metric_matches_its_published_card(tmp_path) -> None:
    database, client = _client(tmp_path)
    _publish_scoped_card(
        database,
        condition_code="COND_DYSLIPIDEMIA",
        scope_key="metric:non_hdl_c",
    )
    response = client.post(
        "/api/evidence/matches",
        json={
            "schema_version": "2",
            "observations": [
                _observation(
                    metric_code="non_hdl_c",
                    value=4.0,
                    reference_low=None,
                    reference_high=3.4,
                    evidence_text="Non-HDL 4.00 mmol/L (<3.40)",
                    observation_id="metric-non-hdl",
                    source_page=1,
                )
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["findings"][0]["card"]["scope_key"] == "metric:non_hdl_c"


def test_same_condition_keeps_each_metric_card_and_source_trace(tmp_path) -> None:
    database, client = _client(tmp_path)
    _publish_scoped_card(
        database,
        condition_code="COND_DYSLIPIDEMIA",
        scope_key="metric:total_cholesterol",
        card_id="card-total-cholesterol",
        profile_id="profile-total-cholesterol",
        topic_id="topic-total-cholesterol",
        claim_id="claim-total-cholesterol",
        paper_id="paper-total-cholesterol",
    )
    _publish_scoped_card(
        database,
        condition_code="COND_DYSLIPIDEMIA",
        scope_key="metric:non_hdl_c",
        card_id="card-non-hdl",
        profile_id="profile-non-hdl",
        topic_id="topic-non-hdl",
        claim_id="claim-non-hdl",
        paper_id="paper-non-hdl",
        version="1.0.1",
        doi="10.1000/test-non-hdl",
    )
    response = client.post(
        "/api/evidence/matches",
        json={
            "schema_version": "2",
            "observations": [
                _observation(
                    observation_id="metric-total-cholesterol",
                    metric_code="total_cholesterol",
                    value=6.2,
                    reference_low=None,
                    reference_high=5.2,
                    evidence_text="总胆固醇 6.2 mmol/L 0-5.2 H",
                ),
                _observation(
                    observation_id="metric-non-hdl",
                    metric_code="non_hdl_c",
                    value=4.2,
                    reference_low=None,
                    reference_high=3.4,
                    evidence_text="Non-HDL 4.2 mmol/L 0-3.4 H",
                ),
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["card"]["id"] for item in body["findings"]} == {
        "card-total-cholesterol",
        "card-non-hdl",
    }
    assert {
        item["source_observation_ids"][0] for item in body["findings"]
    } == {"metric-total-cholesterol", "metric-non-hdl"}
    assert {
        item["card_id"] for item in body["patient_reply"]["findings"]
    } == {"card-total-cholesterol", "card-non-hdl"}


def test_low_card_is_context_only_and_has_no_product_capability(tmp_path) -> None:
    database, client = _client(tmp_path)
    _publish_scoped_card(database, grade="low")

    response = client.post(
        "/api/evidence/matches",
        json={"schema_version": "2", "observations": [_observation()]},
    )

    assert response.status_code == 200
    finding = response.json()["findings"][0]
    assert finding["content_layer"] == "context_only"
    assert finding["action_status"] == "not_available"
    assert "行动建议门槛" in finding["action_message"]
    assert finding["product_status"] == "not_implemented"
    assert finding["card"]["content_layer"] == "context_only"
    assert finding["card"]["action_status"] == "not_available"
    assert response.json()["patient_reply"]["findings"][0]["product_status"] == ("not_implemented")


def test_very_low_published_legacy_card_is_invisible_to_patient_api(tmp_path) -> None:
    database, client = _client(tmp_path)
    _publish_scoped_card(database, grade="very_low")

    response = client.post(
        "/api/evidence/matches",
        json={"schema_version": "2", "observations": [_observation()]},
    )

    assert response.status_code == 200
    assert response.json()["findings"] == []
    assert response.json()["unmatched"][0]["reason"] == "no_published_knowledge_card"


def test_evidence_api_requires_key_and_confirmed_status(tmp_path) -> None:
    _, client = _client(tmp_path, api_key="secret-key-012345678901234")
    payload = {"schema_version": "2", "observations": [_observation()]}
    unauthenticated = TestClient(client.app)
    assert unauthenticated.post("/api/evidence/matches", json=payload).status_code == 401
    assert (
        client.post(
            "/api/evidence/matches",
            json={
                "schema_version": "2",
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
        json={"schema_version": "2", "observations": []},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "correlation ID must be a UUID"


def test_evidence_api_rejects_missing_source_number(tmp_path) -> None:
    _, client = _client(tmp_path)
    response = client.post(
        "/api/evidence/matches",
        json={
            "schema_version": "2",
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


def test_evidence_api_keeps_versioned_response_contract_private(tmp_path) -> None:
    _, client = _client(tmp_path)

    assert client.get("/openapi.json").status_code == 404
    assert EvidenceMatchResponse.model_json_schema()["properties"]["schema_version"] == {
        "const": "2",
        "title": "Schema Version",
        "type": "string",
    }
