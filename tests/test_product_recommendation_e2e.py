import json

from fastapi.testclient import TestClient

from genesis_evidence.core.patient_copy import validate_patient_copy
from genesis_evidence.portal.api import create_app as create_evidence_app
from genesis_evidence.review.api import create_app as create_review_app

from .test_evidence_api import _observation, _publish_scoped_card
from .test_report_assessment import _confirm, _store

EVIDENCE_KEY = "test-evidence-key-with-32-characters"
REVIEW_KEY = "test-review-key-with-32-characters"
REVIEW_HEADERS = {"Authorization": f"Bearer {REVIEW_KEY}"}


def test_upload_confirm_assess_publish_render_and_withdraw(tmp_path) -> None:
    database, report_store, handle = _store(tmp_path)
    _confirm(report_store, handle, value=6.8)
    _publish_scoped_card(
        database,
        condition_code="COND_PREDIABETES",
        scope_key="metric:fasting_glucose",
        card_id="card-e2e",
        profile_id="profile-e2e",
        topic_id="topic-e2e",
        claim_id="claim-e2e",
        paper_id="paper-e2e",
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO product_candidates(
                id, canonical_key, name_zh, content_json, status, created_at, updated_at
            ) VALUES ('product-e2e', 'product-e2e', '膳食纤维营养产品', ?,
                'blocked', 'now', 'now')
            """,
            (json.dumps({"supplier_claims": ["供应商原始宣称"]}, ensure_ascii=False),),
        )

    before_publish = report_store.assess(handle.report_id, handle.access_token)
    assert before_publish["findings"][0]["recommendation_message"] == "暂无推荐"

    review_client = TestClient(
        create_review_app(
            database_path=database.path,
            api_key=REVIEW_KEY,
            reviewer_id="nutrition-reviewer-e2e",
        )
    )
    draft = {
        "condition_codes": ["COND_PREDIABETES"],
        "nutrient": "膳食纤维",
        "reason": "可作为糖代谢相关营养管理的一种膳食补充方向考虑。",
        "safety_message": "请结合个人情况咨询专业人士。",
        "disclaimer": "本建议为健康管理参考，不构成医疗或用药指令。",
        "evidence_links": ["card:card-e2e"],
        "evidence_strength": "moderate",
        "priority": 0,
        "high_risk_marketing_claim": False,
        "note": "端到端审核",
        "decision_ref": "QA-E2E-001",
    }
    assert (
        review_client.put(
            "/api/review/products/product-e2e/recommendation",
            headers=REVIEW_HEADERS,
            json=draft,
        ).json()["status"]
        == "in_review"
    )
    assert (
        review_client.post(
            "/api/review/products/product-e2e/transition",
            headers=REVIEW_HEADERS,
            json={
                "target": "published",
                "note": "批准发布",
                "decision_ref": "QA-E2E-001",
            },
        ).json()["status"]
        == "published"
    )

    after_publish = report_store.assess(handle.report_id, handle.access_token)
    assert after_publish["findings"][0]["product_status"] == "available"
    evidence_client = TestClient(
        create_evidence_app(database_path=database.path, evidence_api_key=EVIDENCE_KEY),
        headers={"X-Genesis-Evidence-Key": EVIDENCE_KEY},
    )
    patient_response = evidence_client.post(
        "/api/evidence/matches",
        json={
            "schema_version": "3",
            "observations": [
                _observation(
                    observation_id="metric-e2e",
                    metric_code="fasting_glucose",
                    value=6.8,
                    reference_low=3.9,
                    reference_high=6.1,
                    evidence_text="空腹血糖 6.8 mmol/L 3.9-6.1 H",
                )
            ],
        },
    )
    assert patient_response.status_code == 200
    patient_finding = patient_response.json()["patient_reply"]["findings"][0]
    assert patient_finding["recommendation_message"] == "以下为可考虑的健康管理建议"
    assert [item["product_name"] for item in patient_finding["recommendations"]] == [
        "膳食纤维营养产品"
    ]
    assert "供应商原始宣称" not in patient_response.text
    for recommendation in patient_finding["recommendations"]:
        validate_patient_copy(recommendation["reason"])
        validate_patient_copy(recommendation["safety_message"])
        validate_patient_copy(recommendation["disclaimer"])

    review_client.post(
        "/api/review/products/product-e2e/transition",
        headers=REVIEW_HEADERS,
        json={
            "target": "withdrawn",
            "note": "端到端下架验证",
            "decision_ref": "QA-E2E-001",
        },
    )
    withdrawn = evidence_client.post(
        "/api/evidence/matches",
        json={
            "schema_version": "3",
            "observations": [
                _observation(
                    observation_id="metric-e2e-withdrawn",
                    metric_code="fasting_glucose",
                    value=6.8,
                    reference_low=3.9,
                    reference_high=6.1,
                    evidence_text="空腹血糖 6.8 mmol/L 3.9-6.1 H",
                )
            ],
        },
    ).json()["patient_reply"]["findings"][0]
    assert withdrawn["recommendations"] == []
    assert withdrawn["recommendation_message"] == "暂无推荐"
