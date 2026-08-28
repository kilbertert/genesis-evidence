import json
from pathlib import Path

import pytest

from genesis_evidence.core.store import Database
from genesis_evidence.products.catalog import (
    LegacyProductCandidate,
    ProductCatalogStore,
    ProductSourceRecord,
    _product_key,
)

SEED_NAMES = (
    "郅臻堂植物甾醇咀嚼片",
    "天然维生素D3片",
    "复合柠檬酸钙",
    "复合骨营养餐",
)


def _legacy_candidates() -> list[LegacyProductCandidate]:
    candidates: list[LegacyProductCandidate] = []
    for index in range(43):
        name = SEED_NAMES[index] if index < len(SEED_NAMES) else f"候选产品{index + 1}"
        key = index + 1
        candidates.append(
            LegacyProductCandidate(
                id=f"candidate-{key}",
                canonical_key=f"legacy-key-{key}",
                name_zh=name,
                name_en=f"Legacy product {key}",
                brand="品牌" if index < 4 else "",
                product_type="nutrition_product",
                supplier_name="供应商",
                manufacturer="生产商",
                origin_market="中国大陆",
                regulatory_status="not_disclosed_in_supplier_material",
                registration_number="",
                review_status="pending_product_review",
                matching_status="blocked_pending_quality_regulatory_medical_review",
                completeness_status="complete",
                content={"name_zh": name, "claim": "供应商宣称，仅供审核"},
                created_at="2026-08-11T00:00:00Z",
                updated_at="2026-08-11T00:00:00Z",
            )
        )
    return candidates


def _legacy_sources(candidates: list[LegacyProductCandidate]) -> list[ProductSourceRecord]:
    return [
        ProductSourceRecord(
            id=f"source-{index + 1}",
            product_id=candidate.id,
            document_id="document-catalog-1",
            page_number=1,
            evidence_type="label",
            field_name="name_zh",
            quote=candidate.name_zh,
            normalized_json=json.dumps({"name_zh": candidate.name_zh}, ensure_ascii=False),
            confidence=1.0,
            locator_json=json.dumps({"page": 1}, ensure_ascii=False),
            source_sha256=f"source-sha-{index + 1:04d}",
            created_at="2026-08-11T00:00:00Z",
        )
        for index, candidate in enumerate(candidates)
    ]


def _migrate(database: Database) -> ProductCatalogStore:
    store = ProductCatalogStore(database)
    candidates = _legacy_candidates()
    store.migrate_from_legacy_rows(candidates, _legacy_sources(candidates))
    return store


def test_product_catalog_schema_initializes_expected_tables(tmp_path: Path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()

    assert {
        "product_candidates",
        "product_candidate_sources",
        "product_recommendations",
        "product_review_audits",
    } <= set(database.table_names())


def test_migration_keeps_43_blocked_and_publishes_four_seed_recommendations(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = _migrate(database)

    with database.connect() as connection:
        blocked = connection.execute(
            "SELECT count(*) FROM product_candidates WHERE status = 'blocked'"
        ).fetchone()[0]
        published = connection.execute(
            "SELECT count(*) FROM product_recommendations WHERE status = 'published'"
        ).fetchone()[0]
        source_count = connection.execute(
            "SELECT count(*) FROM product_candidate_sources"
        ).fetchone()[0]
        orphan_count = connection.execute(
            """
            SELECT count(*) FROM product_candidates
            WHERE NOT EXISTS (
                SELECT 1 FROM product_candidate_sources
                WHERE product_candidate_sources.product_id = product_candidates.id
            )
            """
        ).fetchone()[0]

    assert blocked == 43
    assert published == 4
    assert source_count == 43
    assert orphan_count == 0
    assert store.summary()["blocked_products"] == 43
    assert store.summary()["published_recommendations"] == 4


def test_migration_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    candidates = _legacy_candidates()
    sources = _legacy_sources(candidates)
    store = ProductCatalogStore(database)

    store.migrate_from_legacy_rows(candidates, sources)
    first = _catalog_state(database)
    store.migrate_from_legacy_rows(candidates, sources)
    second = _catalog_state(database)

    assert second == first
    assert first["product_rows"] == 43
    assert first["recommendation_rows"] == 4
    assert first["audit_rows"] == 4


def test_seed_pool_mappings_carry_prd_audit_notes(tmp_path: Path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    _migrate(database)

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT product_id, condition_codes_json, audit_note, decision_ref
            FROM product_recommendations ORDER BY id
            """
        ).fetchall()

    assert [json.loads(row["condition_codes_json"]) for row in rows] == [
        ["COND_DYSLIPIDEMIA"],
        ["COND_VITAMIN_D_DEFICIENCY", "COND_OSTEOPOROSIS_RISK"],
        ["COND_VITAMIN_D_DEFICIENCY", "COND_OSTEOPOROSIS_RISK"],
        ["COND_SARCOPENIA_FRAILTY", "COND_MALNUTRITION_RISK"],
    ]
    assert all("PRD #103" in row["decision_ref"] for row in rows)
    assert all(row["audit_note"].strip() for row in rows)


def test_migration_rolls_back_when_seed_product_is_missing(tmp_path: Path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    candidates = _legacy_candidates()[1:]

    with pytest.raises(ValueError, match="matched 0 candidates"):
        ProductCatalogStore(database).migrate_from_legacy_rows(
            candidates,
            _legacy_sources(candidates),
        )

    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM product_candidates").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM product_candidate_sources").fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT count(*) FROM product_recommendations").fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT count(*) FROM product_review_audits").fetchone()[0]
            == 0
        )


def test_publish_approved_seed_pool_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    candidates = _legacy_candidates()
    store = ProductCatalogStore(database)
    store.migrate_from_legacy_rows(candidates, _legacy_sources(candidates))

    first = _catalog_state(database)
    summary = store.publish_approved_seed_pool()
    second = _catalog_state(database)

    assert summary.published_recommendation_count == 4
    assert second == first


def test_seed_product_matching_normalizes_fullwidth_and_spacing() -> None:
    assert _product_key("郅臻堂植物甾醇咀嚼片") == "郅臻堂植物甾醇"
    assert _product_key(" 天然维生素D3片 ") == "天然维生素D3"
    assert _product_key("天然维生素Ｄ３片") == "天然维生素D3"
    assert _product_key("复合骨营养餐") == "复合骨"


def _catalog_state(database: Database) -> dict[str, object]:
    with database.connect() as connection:
        products = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, canonical_key, name_zh, status, created_at, updated_at
                FROM product_candidates ORDER BY id
                """
            ).fetchall()
        ]
        recommendations = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, product_id, condition_codes_json, status, reviewer,
                       reviewed_at, audit_note, decision_ref, created_at
                FROM product_recommendations ORDER BY id
                """
            ).fetchall()
        ]
        audits = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, product_id, condition_code, action, actor, note,
                       decision_ref, created_at
                FROM product_review_audits ORDER BY id
                """
            ).fetchall()
        ]
    return {
        "product_rows": len(products),
        "recommendation_rows": len(recommendations),
        "audit_rows": len(audits),
        "products": products,
        "recommendations": recommendations,
        "audits": audits,
    }
