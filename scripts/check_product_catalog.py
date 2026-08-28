"""Deterministic catalog backfill check for the product-catalog data layer."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

from genesis_evidence.core.store import Database
from genesis_evidence.products.catalog import ProductCatalogStore

try:
    from migrate_product_catalog import read_legacy_rows
except ModuleNotFoundError:
    from scripts.migrate_product_catalog import read_legacy_rows

EXPECTED_BLOCKED_PRODUCTS = 43
EXPECTED_PUBLISHED_RECOMMENDATIONS = 10
FORBIDDEN_RUNTIME_LEGACY_TOKENS = (
    "genesis-health",
    "literature.db",
    "nutrition_product_candidates",
    "supplier_product_documents",
)


def check_database(database: Database) -> dict[str, int | str]:
    summary = database_checks(database)
    summary["legacy_guard"] = legacy_guard()
    return summary


def database_checks(database: Database) -> dict[str, int | str]:
    summary: dict[str, int | str] = _summary(database)
    if summary["blocked_products"] != EXPECTED_BLOCKED_PRODUCTS:
        raise SystemExit(
            f"blocked products mismatch: expected {EXPECTED_BLOCKED_PRODUCTS}, "
            f"got {summary['blocked_products']}"
        )
    if summary["published_recommendations"] != EXPECTED_PUBLISHED_RECOMMENDATIONS:
        raise SystemExit(
            f"published recommendations mismatch: expected {EXPECTED_PUBLISHED_RECOMMENDATIONS}, "
            f"got {summary['published_recommendations']}"
        )
    if summary["products_without_sources"] != 0:
        raise SystemExit(
            f"products without sources: expected 0, got {summary['products_without_sources']}"
        )
    return summary


def legacy_guard() -> str:
    """Prevent runtime reads of the old genesis-health catalog by the active package."""
    root = Path(__file__).parents[1] / "src" / "genesis_evidence"
    violations: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".html", ".js", ".css", ".ts"}:
            continue
        text = path.read_text(encoding="utf-8").casefold()
        for token in FORBIDDEN_RUNTIME_LEGACY_TOKENS:
            if token in text:
                violations.append(f"{path.relative_to(root)}: {token}")
    if violations:
        raise SystemExit(
            "runtime legacy catalog dependency found:\n" + "\n".join(violations)
        )
    return "ok"


def _summary(database: Database) -> dict[str, int | str]:
    store = ProductCatalogStore(database)
    return store.summary()


def build_fixture_database(directory: Path, target_path: Path) -> Database:
    source_path = directory / "legacy-literature.db"
    with sqlite3.connect(source_path) as connection:
        connection.executescript(
            """
            CREATE TABLE nutrition_product_candidates (
                id TEXT PRIMARY KEY,
                canonical_key TEXT NOT NULL UNIQUE,
                name_zh TEXT NOT NULL,
                name_en TEXT NOT NULL,
                brand TEXT NOT NULL,
                product_type TEXT NOT NULL,
                supplier_name TEXT NOT NULL,
                manufacturer TEXT NOT NULL,
                origin_market TEXT NOT NULL,
                regulatory_status TEXT NOT NULL,
                registration_number TEXT NOT NULL,
                review_status TEXT NOT NULL,
                matching_status TEXT NOT NULL,
                completeness_status TEXT NOT NULL,
                content_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE nutrition_product_candidate_sources (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                evidence_type TEXT NOT NULL,
                field_name TEXT NOT NULL,
                quote TEXT NOT NULL,
                normalized_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                locator_json TEXT NOT NULL,
                source_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            """
        )
        seed_names = (
            "郅臻堂®植物甾醇咀嚼片",
            "天然维生素D3片",
            "复合柠檬酸钙",
            "复合全骨营养餐",
            "复合槲皮素胶囊",
            "奶蓟硫辛酸胶囊",
            "娇韵思®超高浓缩果蔬纤维粉",
            "护心素胶囊",
            "活性叶酸胶囊",
            "超级维BC片",
        )
        candidate_rows = []
        for index in range(43):
            name = seed_names[index] if index < len(seed_names) else f"候选产品{index + 1}"
            candidate_rows.append(
                (
                    f"candidate-{index + 1}",
                    f"legacy-key-{index + 1}",
                    name,
                    "Fixture product",
                    "品牌",
                    "nutrition_product",
                    "供应商",
                    "生产商",
                    "中国大陆",
                    "not_disclosed_in_supplier_material",
                    "",
                    "pending_product_review",
                    "blocked_pending_quality_regulatory_medical_review",
                    "complete",
                    json.dumps({"name_zh": name}, ensure_ascii=False),
                    "2026-08-11T00:00:00Z",
                    "2026-08-11T00:00:00Z",
                )
            )
        connection.executemany(
            """
            INSERT INTO nutrition_product_candidates(
                id, canonical_key, name_zh, name_en, brand, product_type,
                supplier_name, manufacturer, origin_market, regulatory_status,
                registration_number, review_status, matching_status,
                completeness_status, content_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            candidate_rows,
        )
        connection.executemany(
            """
            INSERT INTO nutrition_product_candidate_sources(
                id, candidate_id, document_id, page_number, evidence_type,
                field_name, quote, normalized_json, confidence, locator_json,
                source_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"source-{index + 1}",
                    f"candidate-{index + 1}",
                    "document-catalog-1",
                    1,
                    "label",
                    "name_zh",
                    "源记录",
                    "{}",
                    1.0,
                    "{}",
                    f"source-sha-{index + 1:04d}",
                    "2026-08-11T00:00:00Z",
                )
                for index in range(43)
            ],
        )

    database = Database(target_path)
    database.initialize()
    candidates, sources = read_legacy_rows(source_path)
    ProductCatalogStore(database).migrate_from_legacy_rows(candidates, sources)
    return database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="verify an existing migrated database instead of the self-contained fixture",
    )
    args = parser.parse_args(argv)
    if args.database is not None:
        database = Database(args.database)
        database.initialize()
        summary = check_database(database)
    else:
        with tempfile.TemporaryDirectory() as directory:
            tempdir = Path(directory)
            database = build_fixture_database(tempdir, tempdir / "evidence.sqlite3")
            summary = check_database(database)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
