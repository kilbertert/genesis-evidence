import json

from fastapi.testclient import TestClient

from genesis_evidence.core.store import Database
from genesis_evidence.products.recommendations import load_published_products
from genesis_evidence.review.api import create_app

API_KEY = "test-review-key-with-32-characters"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
REVIEWER = "nutrition-reviewer-1"


def _client(
    tmp_path,
    *,
    supplier_claim: str = "供应商原始宣称",
    risk_flags: list[str] | None = None,
) -> tuple[Database, TestClient]:
    path = tmp_path / "evidence.sqlite3"
    database = Database(path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO product_candidates(
                id, canonical_key, name_zh, content_json, status, created_at, updated_at
            ) VALUES ('product-1', 'product-1', '待审营养产品', ?, 'blocked', 'now', 'now')
            """,
            (
                json.dumps(
                    {
                        "supplier_claims": [supplier_claim],
                        "risk_flags": risk_flags or [],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return database, TestClient(
        create_app(database_path=path, api_key=API_KEY, reviewer_id=REVIEWER)
    )


def _draft(*, high_risk: bool = False) -> dict[str, object]:
    return {
        "condition_codes": ["COND_DYSLIPIDEMIA"],
        "nutrient": "植物甾醇",
        "reason": "可作为血脂相关营养管理的一种膳食补充方向考虑。",
        "safety_message": "请结合个人情况咨询专业人士。",
        "disclaimer": "本建议为健康管理参考，不构成医疗或用药指令。",
        "evidence_links": ["card:card-1"],
        "evidence_strength": "moderate",
        "priority": 0,
        "high_risk_marketing_claim": high_risk,
        "note": "审核患者文案与产品映射。",
        "decision_ref": "PRD #103 / T4",
    }


def test_reviewer_publishes_and_withdraws_product_with_audit(tmp_path) -> None:
    database, client = _client(tmp_path)

    submitted = client.put(
        "/api/review/products/product-1/recommendation",
        headers=HEADERS,
        json=_draft(),
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "in_review"

    published = client.post(
        "/api/review/products/product-1/transition",
        headers=HEADERS,
        json={"target": "published", "note": "批准发布", "decision_ref": "review-1"},
    )
    assert published.status_code == 200
    assert published.json() == {"id": "product-1", "status": "published", "version": 1}
    with database.connect() as connection:
        assert [item["product_id"] for item in load_published_products(connection)] == [
            "product-1"
        ]

    withdrawn = client.post(
        "/api/review/products/product-1/transition",
        headers=HEADERS,
        json={"target": "withdrawn", "note": "产品下架", "decision_ref": "review-2"},
    )
    assert withdrawn.json()["status"] == "withdrawn"
    with database.connect() as connection:
        assert load_published_products(connection) == ()
        audits = connection.execute(
            "SELECT action, actor, note FROM product_review_audits ORDER BY rowid"
        ).fetchall()
    assert [(row["action"], row["actor"]) for row in audits] == [
        ("in_review", REVIEWER),
        ("published", REVIEWER),
        ("withdrawn", REVIEWER),
    ]
    assert audits[-1]["note"] == "产品下架"

    client.post(
        "/api/review/products/product-1/transition",
        headers=HEADERS,
        json={"target": "in_review", "note": "重新复审", "decision_ref": "review-3"},
    )
    republished = client.post(
        "/api/review/products/product-1/transition",
        headers=HEADERS,
        json={"target": "published", "note": "重新发布", "decision_ref": "review-4"},
    )
    assert republished.json()["version"] == 2


def test_high_risk_product_stays_in_review_when_publish_is_requested(tmp_path) -> None:
    database, client = _client(tmp_path)
    client.put(
        "/api/review/products/product-1/recommendation",
        headers=HEADERS,
        json=_draft(high_risk=True),
    )

    response = client.post(
        "/api/review/products/product-1/transition",
        headers=HEADERS,
        json={"target": "published", "note": "尝试批准", "decision_ref": "review-risk"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "in_review"
    products = client.get("/api/review/products", headers=HEADERS).json()
    assert products[0]["high_risk_marketing_claim"] is True
    assert products[0]["status"] == "in_review"
    with database.connect() as connection:
        assert load_published_products(connection) == ()


def test_supplier_claims_automatically_mark_high_risk_products(tmp_path) -> None:
    database, client = _client(tmp_path, supplier_claim="该产品宣称可以治愈并降血糖")
    submitted = client.put(
        "/api/review/products/product-1/recommendation",
        headers=HEADERS,
        json=_draft(high_risk=False),
    )
    assert submitted.status_code == 200

    response = client.post(
        "/api/review/products/product-1/transition",
        headers=HEADERS,
        json={"target": "published", "note": "尝试批准", "decision_ref": "auto-risk"},
    )

    assert response.json()["status"] == "in_review"
    assert client.get("/api/review/products", headers=HEADERS).json()[0][
        "high_risk_marketing_claim"
    ] is True
    with database.connect() as connection:
        assert load_published_products(connection) == ()


def test_risk_flags_automatically_mark_high_risk_products(tmp_path) -> None:
    _, client = _client(tmp_path, risk_flags=["供应商材料包含降血脂宣称"])
    client.put(
        "/api/review/products/product-1/recommendation",
        headers=HEADERS,
        json=_draft(),
    )

    response = client.post(
        "/api/review/products/product-1/transition",
        headers=HEADERS,
        json={"target": "published", "note": "尝试批准", "decision_ref": "risk-flag"},
    )

    assert response.json()["status"] == "in_review"
