"""Deterministic product-recommendation engine.

The engine attaches reviewed product recommendations to an existing confirmed
finding. It performs no disease matching: the caller already supplies the
``condition_code`` chosen by the evidence/finding assembly seam.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

from ..core.patient_copy import validate_patient_copy

EVIDENCE_STRENGTH_RANK = {"high": 0, "moderate": 1, "low": 2, "very_low": 3}
SUPPRESSED_URGENCIES = frozenset({"urgent", "emergency"})
RECOMMENDATION_AVAILABLE_MESSAGE = "以下为可考虑的健康管理建议"
NO_RECOMMENDATION_MESSAGE = "暂无推荐"
SEED_RECOMMENDATION_COPY = {
    "郅臻堂®植物甾醇": {
        "nutrient": "植物甾醇",
        "reason": "该产品方向可作为血脂相关营养管理的一种膳食补充方向考虑。",
        "safety_message": "请在专业人士指导下结合个人情况使用。",
        "disclaimer": "本建议为健康管理参考，不构成医疗或用药指令。",
        "evidence_strength": "moderate",
        "priority": 0,
    },
    "天然维生素D3": {
        "nutrient": "维生素 D3",
        "reason": "该产品方向可为维生素 D 缺乏相关健康风险提供膳食补充参考。",
        "safety_message": "请在专业人士指导下结合个人情况使用。",
        "disclaimer": "本建议为健康管理参考，不构成医疗或用药指令。",
        "evidence_strength": "moderate",
        "priority": 0,
    },
    "复合柠檬酸钙": {
        "nutrient": "钙",
        "reason": "该产品方向可作为骨量健康相关膳食补充方向考虑。",
        "safety_message": "请在专业人士指导下结合个人情况使用。",
        "disclaimer": "本建议为健康管理参考，不构成医疗或用药指令。",
        "evidence_strength": "moderate",
        "priority": 0,
    },
    "复合全骨营养餐": {
        "nutrient": "复合骨营养",
        "reason": "该产品方向可作为肌肉与营养状态管理的膳食支持参考。",
        "safety_message": "请在专业人士指导下结合个人情况使用。",
        "disclaimer": "本建议为健康管理参考，不构成医疗或用药指令。",
        "evidence_strength": "moderate",
        "priority": 0,
    },
}


@dataclass(frozen=True, slots=True)
class Recommendation:
    """A patient-visible recommendation payload for one product."""

    recommendation_id: str
    product_id: str
    product_name: str
    nutrient: str
    reason: str
    safety_message: str
    disclaimer: str
    evidence_links: tuple[str, ...]
    evidence_strength: str
    priority: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def recommend(
    condition_code: str,
    confirmed_observations: Sequence[object],
    *,
    products: Iterable[Mapping[str, object]],
    urgency: str = "routine",
    abnormality_severity: int = 1,
    subject_age: int | None = None,
) -> list[Recommendation]:
    """Return ordered, published, patient-safe recommendations for a finding."""

    del confirmed_observations
    if urgency in SUPPRESSED_URGENCIES or abnormality_severity >= 3:
        return []
    if subject_age is not None and subject_age < 40:
        return []

    matches: list[Recommendation] = []
    for product in products:
        if str(product.get("status", "")).lower() != "published":
            continue
        if product.get("high_risk_marketing_claim"):
            continue
        if condition_code not in product.get("condition_codes", ()):
            continue

        product_id = str(product["product_id"])
        recommendation_id = str(product.get("recommendation_id") or product_id)
        product_name = _required_text(product.get("product_name"), "product_name")
        nutrient = _required_text(product.get("nutrient"), "nutrient")
        reason = _required_text(product.get("reason"), "reason")
        safety_message = _required_text(
            product.get("safety_message"), "safety_message"
        )
        disclaimer = _required_text(product.get("disclaimer"), "disclaimer")
        for label, value in (
            ("reason", reason),
            ("safety_message", safety_message),
            ("disclaimer", disclaimer),
        ):
            try:
                validate_patient_copy(value)
            except ValueError as exc:
                raise ValueError(f"{label}: {exc}") from exc
        evidence_links = tuple(
            dict.fromkeys(
                link_text
                for link in product.get("evidence_links", ())
                if (link_text := str(link).strip())
            )
        )
        if not evidence_links:
            raise ValueError("recommendation requires evidence_links")
        evidence_strength = str(product.get("evidence_strength") or "moderate")
        priority = _non_negative_int(product.get("priority"), default=0)
        matches.append(
            Recommendation(
                recommendation_id=recommendation_id,
                product_id=product_id,
                product_name=product_name,
                nutrient=nutrient,
                reason=reason,
                safety_message=safety_message,
                disclaimer=disclaimer,
                evidence_links=evidence_links,
                evidence_strength=evidence_strength,
                priority=priority,
            )
        )

    matches.sort(
        key=lambda item: (
            item.priority,
            EVIDENCE_STRENGTH_RANK.get(item.evidence_strength, 99),
            item.product_name,
        )
    )
    return matches


def load_published_products(
    connection: sqlite3.Connection,
) -> tuple[dict[str, object], ...]:
    """Load the published recommendation pool and its patient-safe metadata."""

    rows = connection.execute(
        """
        SELECT pr.id AS recommendation_id, pr.product_id, pc.name_zh,
            pr.condition_codes_json, pr.recommendation_json
        FROM product_recommendations pr
        JOIN product_candidates pc ON pc.id = pr.product_id
        WHERE pr.status = 'published'
        ORDER BY pr.reviewed_at, pr.id
        """
    ).fetchall()
    products: list[dict[str, object]] = []
    for row in rows:
        metadata = json.loads(row["recommendation_json"] or "{}")
        if not metadata and str(row["name_zh"]) in SEED_RECOMMENDATION_COPY:
            metadata = seed_recommendation_metadata(str(row["name_zh"]))
        if not metadata.get("nutrient"):
            continue
        products.append(
            {
                "recommendation_id": row["recommendation_id"],
                "product_id": row["product_id"],
                "product_name": row["name_zh"],
                "condition_codes": json.loads(row["condition_codes_json"]),
                "status": "published",
                "high_risk_marketing_claim": bool(
                    metadata.get("high_risk_marketing_claim")
                ),
                "nutrient": metadata.get("nutrient"),
                "reason": metadata.get("reason"),
                "safety_message": metadata.get("safety_message"),
                "disclaimer": metadata.get("disclaimer"),
                "evidence_links": metadata.get("evidence_links", ()),
                "evidence_strength": metadata.get("evidence_strength", "moderate"),
                "priority": metadata.get("priority", 0),
            }
        )
    return tuple(products)


def seed_recommendation_metadata(product_name: str) -> dict[str, object]:
    """Return deterministic, patient-safe metadata for an approved seed product."""

    seed = SEED_RECOMMENDATION_COPY.get(product_name)
    if seed is None:
        raise ValueError(f"no patient-safe metadata for seed product {product_name!r}")
    return {
        **seed,
        "high_risk_marketing_claim": False,
        "evidence_links": ["source:document-catalog-1#page-1"],
    }


def recommendation_message(recommendations: Sequence[object]) -> str:
    return RECOMMENDATION_AVAILABLE_MESSAGE if recommendations else NO_RECOMMENDATION_MESSAGE


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"recommendation requires {field}")
    return text


def _non_negative_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)
