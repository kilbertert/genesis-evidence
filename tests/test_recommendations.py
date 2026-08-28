"""Deterministic product-recommendation engine tests."""

from __future__ import annotations

import pytest

from genesis_evidence.core.store import Database
from genesis_evidence.products.recommendations import (
    load_published_products,
    product_image_url,
    recommend,
    seed_recommendation_metadata,
)


def _observation(**overrides) -> dict[str, object]:
    value = {
        "observation_id": "observation-1",
        "metric_code": "ldl_c",
        "value": 4.2,
        "unit": "mmol/L",
        "reference_low": 0.0,
        "reference_high": 3.4,
    }
    value.update(overrides)
    return value


def _product(**overrides) -> dict[str, object]:
    value = {
        "recommendation_id": "rec-1",
        "product_id": "product-1",
        "product_name": "植物甾醇类产品",
        "condition_codes": ["COND_DYSLIPIDEMIA"],
        "status": "published",
        "nutrient": "植物甾醇",
        "reason": "该产品方向可作为血脂相关营养管理的一种膳食补充方向考虑。",
        "safety_message": "请在专业人士指导下结合个人情况使用。",
        "disclaimer": "本建议为健康管理参考，不构成医疗或用药指令。",
        "evidence_links": ["source:document-1#page-1"],
        "evidence_strength": "moderate",
        "priority": 0,
    }
    value.update(overrides)
    return value


def test_recommend_returns_ordered_published_and_safe_products() -> None:
    products = (
        _product(
            recommendation_id="rec-calcium",
            product_id="product-calcium",
            product_name="复合钙类产品",
            condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
            priority=2,
        ),
        _product(
            recommendation_id="rec-vitamin-d",
            product_id="product-vitamin-d",
            product_name="维生素 D3 类产品",
            condition_codes=["COND_VITAMIN_D_DEFICIENCY"],
            priority=1,
        ),
        _product(
            recommendation_id="rec-dyslipidemia",
            product_id="product-dyslipidemia",
            product_name="血脂方向产品",
            condition_codes=["COND_DYSLIPIDEMIA"],
        ),
    )

    recommendations = recommend(
        "COND_VITAMIN_D_DEFICIENCY",
        [_observation(metric_code="25_oh_vitamin_d")],
        products=products,
    )

    assert [item.product_name for item in recommendations] == [
        "维生素 D3 类产品",
        "复合钙类产品",
    ]
    assert recommendations[0].nutrient
    assert recommendations[0].reason
    assert recommendations[0].safety_message
    assert recommendations[0].disclaimer
    assert recommendations[0].evidence_links
    assert recommendations[0].image_url is None


def test_published_pdf_product_names_resolve_to_same_origin_images() -> None:
    bindings = (
        ("复合全骨营养餐", "/products/whole-bone-nutrition-meal.png"),
        ("复合柠檬酸钙胶囊", "/products/calcium-citrate.png"),
        ("复合槲皮素胶囊", "/products/quercetin.png"),
        ("天然维生素D3片", "/products/vitamin-d3.png"),
        ("奶蓟硫辛酸胶囊", "/products/milk-thistle-alpha-lipoic.png"),
        ("娇韵思®超高浓缩果蔬纤维粉", "/products/joyees-fruit-vegetable-fiber.png"),
        ("护心素胶囊", "/products/cardiotonic-element.png"),
        ("活性叶酸胶囊", "/products/active-folate.png"),
        ("超级维BC片", "/products/super-bc.png"),
        ("郅臻堂®植物甾醇咀嚼片", "/products/zhizhen-plant-sterol.png"),
    )

    for name, expected_url in bindings:
        assert product_image_url(name) == expected_url
        assert seed_recommendation_metadata(name)["image_url"] == expected_url
    assert product_image_url("郅臻堂 ® 植物甾醇咀嚼片") == bindings[-1][1]


def test_recommend_applies_defaults_and_sorts_equal_priority_by_evidence() -> None:
    recommendations = recommend(
        "COND_DYSLIPIDEMIA",
        [_observation()],
        products=(
            _product(
                recommendation_id=None,
                product_id="high-evidence",
                product_name="高证据产品",
                evidence_strength="high",
                priority=None,
            ),
            _product(
                product_id="low-evidence",
                product_name="低证据产品",
                evidence_strength="low",
                priority=None,
            ),
            _product(
                product_id="mixed-evidence",
                product_name="混合证据产品",
                evidence_strength="mixed",
                priority=None,
            ),
        ),
    )

    assert [item.product_id for item in recommendations] == [
        "high-evidence",
        "low-evidence",
        "mixed-evidence",
    ]
    assert recommendations[0].recommendation_id == "high-evidence"
    assert recommendations[0].priority == 0


def test_recommend_excludes_unpublished_withdrawn_and_high_risk_products() -> None:
    products = (
        _product(product_id="published-safe"),
        _product(product_id="blocked", status="blocked"),
        _product(product_id="in-review", status="in_review"),
        _product(product_id="withdrawn", status="withdrawn"),
        _product(product_id="risky", high_risk_marketing_claim=True),
    )

    recommendations = recommend(
        "COND_DYSLIPIDEMIA",
        [_observation()],
        products=products,
    )

    assert [item.product_id for item in recommendations] == ["published-safe"]


def test_recommend_suppresses_urgent_emergency_and_high_severity_findings() -> None:
    products = (_product(),)

    assert recommend("COND_DYSLIPIDEMIA", [_observation()], products=products) != []
    assert (
        recommend("COND_DYSLIPIDEMIA", [_observation()], products=products, urgency="urgent")
        == []
    )
    assert (
        recommend(
            "COND_DYSLIPIDEMIA",
            [_observation()],
            products=products,
            urgency="emergency",
        )
        == []
    )
    assert (
        recommend(
            "COND_DYSLIPIDEMIA",
            [_observation()],
            products=products,
            abnormality_severity=3,
        )
        == []
    )


def test_recommend_requires_subject_to_be_age_40_or_older_when_age_is_known() -> None:
    products = (_product(),)

    assert recommend("COND_DYSLIPIDEMIA", [_observation()], products=products) != []
    assert (
        recommend("COND_DYSLIPIDEMIA", [_observation()], products=products, subject_age=40)
        != []
    )
    assert (
        recommend("COND_DYSLIPIDEMIA", [_observation()], products=products, subject_age=39)
        == []
    )


def test_recommend_returns_empty_for_unmapped_condition() -> None:
    recommendations = recommend(
        "COND_CHRONIC_CONSTIPATION",
        [_observation()],
        products=(_product(),),
    )

    assert recommendations == []


def test_recommend_rejects_forbidden_patient_copy() -> None:
    with pytest.raises(ValueError, match="forbidden term"):
        recommend(
            "COND_DYSLIPIDEMIA",
            [_observation()],
            products=(_product(reason="可治愈高血脂"),),
        )


def test_recommend_rejects_empty_evidence_links() -> None:
    with pytest.raises(ValueError, match="evidence_links"):
        recommend(
            "COND_DYSLIPIDEMIA",
            [_observation()],
            products=(_product(evidence_links=[" "]),),
        )


def test_old_published_seed_rows_receive_recommendation_metadata_after_upgrade(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO product_candidates(
                id, canonical_key, name_zh, status, created_at, updated_at
            ) VALUES ('seed-d3', 'seed-d3', '天然维生素Ｄ３片', 'blocked', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO product_recommendations(
                id, product_id, condition_codes_json, status, reviewer,
                reviewed_at, audit_note, decision_ref, created_at
            ) VALUES (
                'seed-recommendation:seed-d3', 'seed-d3',
                '["COND_VITAMIN_D_DEFICIENCY"]', 'published', 'seed-reviewer',
                'now', 'approved seed', 'PRD #103', 'now'
            )
            """
        )

    with database.connect() as connection:
        products = load_published_products(connection)

    assert products[0]["nutrient"] == "维生素 D3"
    assert products[0]["image_url"] == "/products/vitamin-d3.png"
