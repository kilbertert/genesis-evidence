"""Blocked product candidates and the approved recommendation seed pool."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from ..core.store.database import Database

_COMMON_PRODUCT_SUFFIXES = (
    "咀嚼片",
    "软胶囊",
    "胶囊",
    "营养餐",
    "冲剂",
    "颗粒",
    "片",
    "粉",
)
SEED_REVIEWER = "system:seed-pool"
SEED_DECISION_REF = "PRD #103 初选最小产品池"
SEED_REVIEWED_AT = "2026-08-28T00:00:00Z"
_SEED_AUDIT_NOTE = "已批准最小产品池，将其发布为可推荐种子。"


@dataclass(frozen=True, slots=True)
class LegacyProductCandidate:
    id: str
    canonical_key: str
    name_zh: str
    name_en: str
    brand: str
    product_type: str
    supplier_name: str
    manufacturer: str
    origin_market: str
    regulatory_status: str
    registration_number: str
    review_status: str
    matching_status: str
    completeness_status: str
    content: dict[str, object]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ProductSourceRecord:
    id: str
    product_id: str
    document_id: str
    page_number: int
    evidence_type: str
    field_name: str
    quote: str
    normalized_json: str
    confidence: float
    locator_json: str
    source_sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class SeedProductMapping:
    product_name: str
    condition_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SeedPublicationSummary:
    published_recommendation_count: int
    product_ids: tuple[str, ...]
    recommendation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyCatalogMigrationSummary:
    imported_products: int
    imported_sources: int
    published_recommendation_count: int


SEED_PRODUCT_MAPPINGS = (
    SeedProductMapping("郅臻堂植物甾醇", ("COND_DYSLIPIDEMIA",)),
    SeedProductMapping(
        "天然维生素D3",
        ("COND_VITAMIN_D_DEFICIENCY", "COND_OSTEOPOROSIS_RISK"),
    ),
    SeedProductMapping(
        "复合柠檬酸钙",
        ("COND_VITAMIN_D_DEFICIENCY", "COND_OSTEOPOROSIS_RISK"),
    ),
    SeedProductMapping(
        "复合骨营养餐",
        ("COND_SARCOPENIA_FRAILTY", "COND_MALNUTRITION_RISK"),
    ),
)


class ProductCatalogStore:
    """Persistence boundary for candidate products and published recommendations."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def migrate_from_legacy_rows(
        self,
        candidates: Iterable[LegacyProductCandidate],
        sources: Iterable[ProductSourceRecord],
    ) -> LegacyCatalogMigrationSummary:
        candidate_rows = list(candidates)
        source_rows = list(sources)
        with self.database.transaction() as connection:
            imported_products = self._upsert_blocked_candidates(connection, candidate_rows)
            imported_sources = self._upsert_sources(connection, source_rows)
            seed_summary = self._publish_seed_pool(connection)
        return LegacyCatalogMigrationSummary(
            imported_products=imported_products,
            imported_sources=imported_sources,
            published_recommendation_count=seed_summary.published_recommendation_count,
        )

    def publish_approved_seed_pool(self) -> SeedPublicationSummary:
        with self.database.transaction() as connection:
            return self._publish_seed_pool(connection)

    def summary(self) -> dict[str, int]:
        with self.database.connect() as connection:
            return {
                "blocked_products": connection.execute(
                    "SELECT count(*) FROM product_candidates WHERE status = 'blocked'"
                ).fetchone()[0],
                "published_recommendations": connection.execute(
                    "SELECT count(*) FROM product_recommendations WHERE status = 'published'"
                ).fetchone()[0],
                "source_records": connection.execute(
                    "SELECT count(*) FROM product_candidate_sources"
                ).fetchone()[0],
                "products_without_sources": connection.execute(
                    """
                    SELECT count(*) FROM product_candidates
                    WHERE NOT EXISTS (
                        SELECT 1 FROM product_candidate_sources
                        WHERE product_candidate_sources.product_id = product_candidates.id
                    )
                    """
                ).fetchone()[0],
                "published_audits": connection.execute(
                    "SELECT count(*) FROM product_review_audits WHERE action = 'published'"
                ).fetchone()[0],
            }

    def _upsert_blocked_candidates(
        self, connection: sqlite3.Connection, candidates: list[LegacyProductCandidate]
    ) -> int:
        connection.executemany(
            """
            INSERT INTO product_candidates(
                id, canonical_key, name_zh, name_en, brand, product_type,
                supplier_name, manufacturer, origin_market, regulatory_status,
                registration_number, review_status, matching_status,
                completeness_status, content_json, status, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'blocked', ?, ?
            )
            ON CONFLICT(canonical_key) DO UPDATE SET
                name_zh = excluded.name_zh,
                name_en = excluded.name_en,
                brand = excluded.brand,
                product_type = excluded.product_type,
                supplier_name = excluded.supplier_name,
                manufacturer = excluded.manufacturer,
                origin_market = excluded.origin_market,
                regulatory_status = excluded.regulatory_status,
                registration_number = excluded.registration_number,
                review_status = excluded.review_status,
                matching_status = excluded.matching_status,
                completeness_status = excluded.completeness_status,
                content_json = excluded.content_json,
                updated_at = excluded.updated_at
            """,
            [
                (
                    candidate.id,
                    candidate.canonical_key,
                    candidate.name_zh,
                    candidate.name_en,
                    candidate.brand,
                    candidate.product_type,
                    candidate.supplier_name,
                    candidate.manufacturer,
                    candidate.origin_market,
                    candidate.regulatory_status,
                    candidate.registration_number,
                    candidate.review_status,
                    candidate.matching_status,
                    candidate.completeness_status,
                    json.dumps(candidate.content, ensure_ascii=False, sort_keys=True),
                    candidate.created_at,
                    candidate.updated_at,
                )
                for candidate in candidates
            ],
        )
        return len(candidates)

    def _upsert_sources(
        self, connection: sqlite3.Connection, sources: list[ProductSourceRecord]
    ) -> int:
        connection.executemany(
            """
            INSERT INTO product_candidate_sources(
                id, product_id, document_id, page_number, evidence_type, field_name,
                quote, normalized_json, confidence, locator_json, source_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                product_id = excluded.product_id,
                document_id = excluded.document_id,
                page_number = excluded.page_number,
                evidence_type = excluded.evidence_type,
                field_name = excluded.field_name,
                quote = excluded.quote,
                normalized_json = excluded.normalized_json,
                confidence = excluded.confidence,
                locator_json = excluded.locator_json,
                source_sha256 = excluded.source_sha256,
                created_at = excluded.created_at
            """,
            [
                (
                    source.id,
                    source.product_id,
                    source.document_id,
                    source.page_number,
                    source.evidence_type,
                    source.field_name,
                    source.quote,
                    source.normalized_json,
                    source.confidence,
                    source.locator_json,
                    source.source_sha256,
                    source.created_at,
                )
                for source in sources
            ],
        )
        return len(sources)

    def _publish_seed_pool(self, connection: sqlite3.Connection) -> SeedPublicationSummary:
        product_ids: list[str] = []
        recommendation_ids: list[str] = []
        for mapping in SEED_PRODUCT_MAPPINGS:
            product_id = self._find_seed_product_id(connection, mapping.product_name)
            product_ids.append(product_id)
            recommendation_id = f"seed-recommendation:{product_id}"
            recommendation_ids.append(recommendation_id)
            condition_codes_json = json.dumps(
                list(mapping.condition_codes), ensure_ascii=False, sort_keys=True
            )
            audit_note = _seed_audit_note(mapping.product_name, mapping.condition_codes)
            connection.execute(
                """
                INSERT INTO product_recommendations(
                    id, product_id, condition_codes_json, status, reviewer,
                    reviewed_at, audit_note, decision_ref, created_at
                ) VALUES (?, ?, ?, 'published', ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    condition_codes_json = excluded.condition_codes_json,
                    status = 'published',
                    reviewer = excluded.reviewer,
                    reviewed_at = excluded.reviewed_at,
                    audit_note = excluded.audit_note,
                    decision_ref = excluded.decision_ref,
                    created_at = excluded.created_at
                """,
                (
                    recommendation_id,
                    product_id,
                    condition_codes_json,
                    SEED_REVIEWER,
                    SEED_REVIEWED_AT,
                    audit_note,
                    SEED_DECISION_REF,
                    SEED_REVIEWED_AT,
                ),
            )
            connection.execute(
                """
                INSERT INTO product_review_audits(
                    id, product_id, condition_code, action, actor, note,
                    decision_ref, created_at
                ) VALUES (?, ?, NULL, 'published', ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    product_id = excluded.product_id,
                    condition_code = NULL,
                    action = 'published',
                    actor = excluded.actor,
                    note = excluded.note,
                    decision_ref = excluded.decision_ref,
                    created_at = excluded.created_at
                """,
                (
                    f"seed-audit:{product_id}",
                    product_id,
                    SEED_REVIEWER,
                    audit_note,
                    SEED_DECISION_REF,
                    SEED_REVIEWED_AT,
                ),
            )
        return SeedPublicationSummary(
            published_recommendation_count=len(product_ids),
            product_ids=tuple(product_ids),
            recommendation_ids=tuple(recommendation_ids),
        )

    def _find_seed_product_id(self, connection: sqlite3.Connection, product_name: str) -> str:
        target_key = _product_key(product_name)
        rows = connection.execute("SELECT id, name_zh FROM product_candidates").fetchall()
        matches = [
            str(row["id"])
            for row in rows
            if _product_key(str(row["name_zh"])) == target_key
        ]
        if len(matches) != 1:
            raise ValueError(
                f"seed product {product_name!r} matched {len(matches)} candidates; expected 1"
            )
        return matches[0]


def _product_key(name: str) -> str:
    key = unicodedata.normalize("NFKC", name).replace(" ", "").strip()
    for suffix in _COMMON_PRODUCT_SUFFIXES:
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    return key


def _seed_audit_note(product_name: str, condition_codes: tuple[str, ...]) -> str:
    conditions = "、".join(condition_codes)
    return f"{_SEED_AUDIT_NOTE}产品：{product_name}；健康风险映射：{conditions}。"
