"""One-time backfill of legacy product candidates into the self-owned catalog."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from genesis_evidence.core.store import Database
from genesis_evidence.products.catalog import (
    LegacyProductCandidate,
    ProductCatalogStore,
    ProductSourceRecord,
)


def _json_object(value: object) -> dict[str, object]:
    if value in (None, ""):
        return {}
    payload = json.loads(str(value))
    if not isinstance(payload, dict):
        raise ValueError("legacy product JSON field must be an object")
    return payload


def read_legacy_rows(
    source_path: Path,
) -> tuple[list[LegacyProductCandidate], list[ProductSourceRecord]]:
    """Read the frozen legacy candidate and source tables without mutating them."""
    with sqlite3.connect(source_path) as connection:
        connection.row_factory = sqlite3.Row
        candidate_rows = connection.execute(
            """
            SELECT id, canonical_key, name_zh, name_en, brand, product_type,
                   supplier_name, manufacturer, origin_market, regulatory_status,
                   registration_number, review_status, matching_status,
                   completeness_status, content_json, created_at, updated_at
            FROM nutrition_product_candidates
            ORDER BY canonical_key
            """
        ).fetchall()
        source_rows = connection.execute(
            """
            SELECT id, candidate_id, document_id, page_number, evidence_type,
                   field_name, quote, normalized_json, confidence, locator_json,
                   source_sha256, created_at
            FROM nutrition_product_candidate_sources
            ORDER BY candidate_id, page_number, id
            """
        ).fetchall()

    candidates = [
        LegacyProductCandidate(
            id=str(row["id"]),
            canonical_key=str(row["canonical_key"]),
            name_zh=str(row["name_zh"]),
            name_en=str(row["name_en"]),
            brand=str(row["brand"]),
            product_type=str(row["product_type"]),
            supplier_name=str(row["supplier_name"]),
            manufacturer=str(row["manufacturer"]),
            origin_market=str(row["origin_market"]),
            regulatory_status=str(row["regulatory_status"]),
            registration_number=str(row["registration_number"]),
            review_status=str(row["review_status"]),
            matching_status=str(row["matching_status"]),
            completeness_status=str(row["completeness_status"]),
            content=_json_object(row["content_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
        for row in candidate_rows
    ]
    sources = [
        ProductSourceRecord(
            id=str(row["id"]),
            product_id=str(row["candidate_id"]),
            document_id=str(row["document_id"]),
            page_number=int(row["page_number"]),
            evidence_type=str(row["evidence_type"]),
            field_name=str(row["field_name"]),
            quote=str(row["quote"]),
            normalized_json=str(row["normalized_json"] or "{}"),
            confidence=float(row["confidence"]),
            locator_json=str(row["locator_json"] or "{}"),
            source_sha256=str(row["source_sha256"]),
            created_at=str(row["created_at"]),
        )
        for row in source_rows
    ]
    return candidates, sources


def build_parser() -> argparse.ArgumentParser:
    default_source = (
        Path(__file__).resolve().parents[1].parent
        / "genesis-health"
        / "var"
        / "literature"
        / "literature.db"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-db",
        type=Path,
        default=default_source,
        help="legacy literature.db to backfill once (default: %(default)s)",
    )
    parser.add_argument(
        "--target-db",
        type=Path,
        default=Path(os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3")),
        help="self-owned evidence SQLite database (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_path: Path = args.source_db
    target_path: Path = args.target_db
    if not source_path.is_file():
        raise SystemExit(f"legacy source database not found: {source_path}")

    database = Database(target_path)
    database.initialize()
    candidates, sources = read_legacy_rows(source_path)
    store = ProductCatalogStore(database)
    summary = store.migrate_from_legacy_rows(candidates, sources)
    print(
        json.dumps(
            {
                "imported_products": summary.imported_products,
                "imported_sources": summary.imported_sources,
                "published_recommendations": summary.published_recommendation_count,
                **store.summary(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
