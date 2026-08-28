"""Blocked product candidates and the approved recommendation seed pool."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from ..core.patient_copy import validate_patient_copy
from ..core.store.database import Database
from .recommendations import seed_recommendation_metadata

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
_HIGH_RISK_MARKETING_TERMS = (
    "溶血栓",
    "降血糖",
    "降血压",
    "降血脂",
    "治愈",
    "根治",
    "排毒",
    "抗癌",
    "逆龄",
    "逆糖",
)
_ALLOWED_RECOMMENDATION_TRANSITIONS = {
    "blocked": {"in_review"},
    "in_review": {"blocked", "in_review", "published"},
    "published": {"withdrawn"},
    "withdrawn": {"in_review"},
}


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
    SeedProductMapping("郅臻堂®植物甾醇", ("COND_DYSLIPIDEMIA",)),
    SeedProductMapping(
        "天然维生素D3",
        ("COND_VITAMIN_D_DEFICIENCY", "COND_OSTEOPOROSIS_RISK"),
    ),
    SeedProductMapping(
        "复合柠檬酸钙",
        ("COND_VITAMIN_D_DEFICIENCY", "COND_OSTEOPOROSIS_RISK"),
    ),
    SeedProductMapping(
        "复合全骨营养餐",
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

    def list_review_products(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT pc.id, pc.name_zh, pc.status AS candidate_status,
                    pr.condition_codes_json, pr.recommendation_json, pr.status
                FROM product_candidates pc
                LEFT JOIN product_recommendations pr ON pr.product_id = pc.id
                ORDER BY pc.name_zh, pc.id
                """
            ).fetchall()
        products = []
        for row in rows:
            metadata = json.loads(row["recommendation_json"] or "{}")
            status = row["status"] or row["candidate_status"]
            products.append(
                {
                    "id": row["id"],
                    "name": row["name_zh"],
                    "status": status,
                    "condition_codes": json.loads(row["condition_codes_json"] or "[]"),
                    "high_risk_marketing_claim": bool(
                        metadata.get("high_risk_marketing_claim")
                    ),
                    "version": int(metadata.get("version", 1 if status == "published" else 0)),
                    "recommendation": metadata,
                }
            )
        return products

    def submit_recommendation(
        self,
        product_id: str,
        *,
        condition_codes: list[str],
        recommendation: dict[str, object],
        actor: str,
        note: str,
        decision_ref: str,
    ) -> dict[str, object]:
        if not condition_codes:
            raise ValueError("condition_codes must contain known conditions")
        for field in ("reason", "safety_message", "disclaimer"):
            validate_patient_copy(str(recommendation.get(field) or ""))
        if not recommendation.get("evidence_links"):
            raise ValueError("recommendation requires evidence_links")
        now = _now()
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT id, content_json FROM product_candidates WHERE id = ?", (product_id,)
            ).fetchone()
            if product is None:
                raise ValueError("product not found")
            known_conditions = {
                row[0]
                for row in connection.execute(
                    "SELECT code FROM conditions WHERE code IN "
                    f"({','.join('?' for _ in condition_codes)})",
                    tuple(condition_codes),
                ).fetchall()
            }
            if known_conditions != set(condition_codes):
                raise ValueError("condition_codes must contain known conditions")
            existing = connection.execute(
                """
                SELECT status, recommendation_json
                FROM product_recommendations WHERE product_id = ?
                """,
                (product_id,),
            ).fetchone()
            if existing is not None and existing["status"] == "published":
                raise ValueError("withdraw a published recommendation before replacing it")
            previous = json.loads(existing["recommendation_json"] or "{}") if existing else {}
            metadata = {
                **recommendation,
                "high_risk_marketing_claim": bool(
                    recommendation.get("high_risk_marketing_claim")
                    or _has_high_risk_marketing_claim(product["content_json"])
                ),
                "version": int(previous.get("version", 0)),
            }
            connection.execute(
                """
                INSERT INTO product_recommendations(
                    id, product_id, condition_codes_json, recommendation_json,
                    status, reviewer, reviewed_at, audit_note, decision_ref, created_at
                ) VALUES (?, ?, ?, ?, 'in_review', ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    condition_codes_json = excluded.condition_codes_json,
                    recommendation_json = excluded.recommendation_json,
                    status = 'in_review', reviewer = excluded.reviewer,
                    reviewed_at = excluded.reviewed_at, audit_note = excluded.audit_note,
                    decision_ref = excluded.decision_ref
                """,
                (
                    f"recommendation:{product_id}",
                    product_id,
                    json.dumps(list(dict.fromkeys(condition_codes)), ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    actor,
                    now,
                    note,
                    decision_ref,
                    now,
                ),
            )
            connection.execute(
                "UPDATE product_candidates SET status = 'in_review', updated_at = ? WHERE id = ?",
                (now, product_id),
            )
            self._audit(connection, product_id, "in_review", actor, note, decision_ref, now)
        return {"id": product_id, "status": "in_review", "version": metadata["version"]}

    def transition_recommendation(
        self,
        product_id: str,
        *,
        target: str,
        actor: str,
        note: str,
        decision_ref: str,
    ) -> dict[str, object]:
        if target not in {"blocked", "in_review", "published", "withdrawn"}:
            raise ValueError("unsupported product transition")
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT status, recommendation_json
                FROM product_recommendations WHERE product_id = ?
                """,
                (product_id,),
            ).fetchone()
            if row is None:
                raise ValueError("product recommendation not found")
            if target not in _ALLOWED_RECOMMENDATION_TRANSITIONS[row["status"]]:
                raise ValueError(f"invalid product transition: {row['status']} -> {target}")
            metadata = json.loads(row["recommendation_json"] or "{}")
            actual_target = target
            if target == "published" and metadata.get("high_risk_marketing_claim"):
                actual_target = "in_review"
                note = f"{note}；风险标产品保留在需复审池。"
            version = int(metadata.get("version", 1 if row["status"] == "published" else 0))
            if actual_target == "published" and row["status"] != "published":
                version += 1
            metadata["version"] = version
            connection.execute(
                """
                UPDATE product_recommendations
                SET status = ?, recommendation_json = ?, reviewer = ?, reviewed_at = ?,
                    audit_note = ?, decision_ref = ?
                WHERE product_id = ?
                """,
                (
                    actual_target,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    actor,
                    now,
                    note,
                    decision_ref,
                    product_id,
                ),
            )
            connection.execute(
                "UPDATE product_candidates SET status = ?, updated_at = ? WHERE id = ?",
                (actual_target, now, product_id),
            )
            self._audit(
                connection, product_id, actual_target, actor, note, decision_ref, now
            )
        return {"id": product_id, "status": actual_target, "version": version}

    def list_mapping_drafts(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*, pc.name_zh
                FROM product_mapping_drafts d
                JOIN product_candidates pc ON pc.id = d.product_id
                ORDER BY d.condition_code, d.id
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "condition_code": row["condition_code"],
                "functional_direction": row["functional_direction"],
                "functional_category": row["functional_category"],
                "product_id": row["product_id"],
                "product_name": row["name_zh"],
                "status": row["status"],
                "source_ref": row["source_ref"],
                "draft_method": row["draft_method"],
                "recommendation": json.loads(row["recommendation_json"] or "{}"),
            }
            for row in rows
        ]

    def transition_mapping_draft(
        self,
        draft_id: str,
        *,
        target: str,
        actor: str,
        note: str,
        decision_ref: str,
    ) -> dict[str, object]:
        if target not in {"published", "rejected", "needs_more_info"}:
            raise ValueError("unsupported mapping transition")
        now = _now()
        version = 0
        with self.database.transaction() as connection:
            draft = connection.execute(
                "SELECT * FROM product_mapping_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
            if draft is None:
                raise ValueError("product mapping draft not found")
            if draft["status"] not in {"in_review", "needs_more_info"}:
                raise ValueError(f"mapping draft is already {draft['status']}")
            metadata = json.loads(draft["recommendation_json"] or "{}")
            actual_target = target
            if target == "published" and metadata.get("high_risk_marketing_claim"):
                actual_target = "needs_more_info"
                note = f"{note}；风险标产品保留在需复审池。"
            if actual_target == "published":
                existing = connection.execute(
                    """
                    SELECT condition_codes_json, recommendation_json, status
                    FROM product_recommendations WHERE product_id = ?
                    """,
                    (draft["product_id"],),
                ).fetchone()
                condition_codes = set()
                if existing is not None and existing["status"] == "published":
                    condition_codes.update(json.loads(existing["condition_codes_json"] or "[]"))
                condition_codes.add(str(draft["condition_code"]))
                if existing is not None and existing["status"] == "published":
                    metadata = json.loads(existing["recommendation_json"] or "{}")
                version = int(metadata.get("version", 0)) + 1
                metadata["version"] = version
                connection.execute(
                    """
                    INSERT INTO product_recommendations(
                        id, product_id, condition_codes_json, recommendation_json,
                        status, reviewer, reviewed_at, audit_note, decision_ref, created_at
                    ) VALUES (?, ?, ?, ?, 'published', ?, ?, ?, ?, ?)
                    ON CONFLICT(product_id) DO UPDATE SET
                        condition_codes_json = excluded.condition_codes_json,
                        recommendation_json = excluded.recommendation_json,
                        status = 'published', reviewer = excluded.reviewer,
                        reviewed_at = excluded.reviewed_at, audit_note = excluded.audit_note,
                        decision_ref = excluded.decision_ref
                    """,
                    (
                        f"recommendation:{draft['product_id']}",
                        draft["product_id"],
                        json.dumps(sorted(condition_codes), ensure_ascii=False),
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        actor,
                        now,
                        note,
                        decision_ref,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE product_candidates
                    SET status = 'published', updated_at = ? WHERE id = ?
                    """,
                    (now, draft["product_id"]),
                )
            connection.execute(
                """
                UPDATE product_mapping_drafts
                SET status = ?, reviewer = ?, reviewed_at = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (actual_target, actor, now, note, now, draft_id),
            )
            audit_action = {
                "published": "published",
                "rejected": "blocked",
                "needs_more_info": "in_review",
            }[actual_target]
            self._audit(
                connection,
                str(draft["product_id"]),
                audit_action,
                actor,
                note,
                decision_ref,
                now,
                condition_code=str(draft["condition_code"]),
            )
        return {"id": draft_id, "status": actual_target, "version": version}

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        product_id: str,
        action: str,
        actor: str,
        note: str,
        decision_ref: str,
        created_at: str,
        condition_code: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO product_review_audits(
                id, product_id, condition_code, action, actor, note, decision_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                product_id,
                condition_code,
                action,
                actor,
                note,
                decision_ref,
                created_at,
            ),
        )

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
            recommendation_json = json.dumps(
                seed_recommendation_metadata(mapping.product_name),
                ensure_ascii=False,
                sort_keys=True,
            )
            connection.execute(
                """
                INSERT INTO product_recommendations(
                    id, product_id, condition_codes_json, recommendation_json,
                    status, reviewer, reviewed_at, audit_note, decision_ref, created_at
                ) VALUES (?, ?, ?, ?, 'published', ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    condition_codes_json = excluded.condition_codes_json,
                    recommendation_json = excluded.recommendation_json,
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
                    recommendation_json,
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


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _has_high_risk_marketing_claim(content_json: str) -> bool:
    try:
        content = json.loads(content_json or "{}")
    except json.JSONDecodeError:
        content = content_json
    if isinstance(content, dict):
        content = {
            "supplier_claims": content.get("supplier_claims", []),
            "risk_flags": content.get("risk_flags", []),
        }
    text = unicodedata.normalize(
        "NFKC", json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
    )
    return any(term in text for term in _HIGH_RISK_MARKETING_TERMS)
