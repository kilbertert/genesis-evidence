"""Read-only published-evidence query store for Health-Flow."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from ...products.recommendations import (
    load_published_products,
    recommend,
    recommendation_message,
)
from ..conditions import CONDITION_BY_CODE, CONDITIONS
from ..contracts import EvidenceMatchObservation, card_capabilities
from ..metrics import METRIC_LABELS, evidence_contains_value
from .database import Database

METRIC_CODES = frozenset(metric for condition in CONDITIONS for metric in condition.metrics)
CONDITIONS_BY_METRIC = {
    metric: tuple(condition for condition in CONDITIONS if metric in condition.metrics)
    for metric in METRIC_CODES
}
EVIDENCE_RANK = {"high": 0, "moderate": 1, "low": 2, "very_low": 3}
ASSESSMENT_SORTING_VERSION = "published-card-reference-range-v1"


class EvidenceStore:
    """Match confirmed external observations to scoped, published cards only."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def match_published_cards(
        self,
        observations: Sequence[EvidenceMatchObservation],
        *,
        correlation_id: str,
        actor: str = "health-flow",
        schema_version: str = "3",
    ) -> dict[str, object]:
        with self.database.transaction() as connection:
            cards = _published_cards(connection)
            published_products = load_published_products(connection)
            findings_by_condition: dict[str, dict[str, object]] = {}
            unmatched: list[dict[str, object]] = []
            skipped: list[dict[str, object]] = []
            abnormal_count = 0
            for observation in observations:
                _validate_observation(observation)
                if observation.reference_low is None and observation.reference_high is None:
                    skipped.append(
                        {
                            "observation_id": observation.observation_id,
                            "reason": "missing_reference_range",
                        }
                    )
                    continue
                if not _is_abnormal(observation):
                    skipped.append(
                        {
                            "observation_id": observation.observation_id,
                            "reason": "within_reference_range",
                        }
                    )
                    continue
                abnormal_count += 1
                missing: list[str] = []
                source = _source_observation(observation)
                for condition in CONDITIONS_BY_METRIC[observation.metric_code]:
                    card = cards.get((condition.code, f"metric:{observation.metric_code}"))
                    if card is None:
                        missing.append(condition.code)
                        continue
                    scope_key = str(card["scope_key"])
                    finding = findings_by_condition.setdefault(
                        condition.code,
                        {
                            "condition_code": condition.code,
                            "condition_name": condition.name,
                            "source_observation_ids": [],
                            "urgency": "routine",
                            "abnormality_severity": 1,
                            "evidence_strength": card["grade"],
                            "needs_recheck": True,
                            "department": condition.department,
                            "recheck_direction": condition.recheck_direction,
                            "epidemiology_background": "",
                            "source_observations": [],
                            "_evidence_items": {},
                            "content_layer": card["content_layer"],
                            "action_status": card["action_status"],
                            "action_message": card["action_message"],
                            "product_status": card["product_status"],
                        },
                    )
                    _append_unique(finding["source_observation_ids"], observation.observation_id)
                    _append_unique(finding["source_observations"], source)
                    evidence_items = finding["_evidence_items"]
                    evidence_item = evidence_items.setdefault(
                        scope_key,
                        {
                            "metric_code": observation.metric_code,
                            "metric_label": METRIC_LABELS[observation.metric_code],
                            "card": card,
                            "evidence_strength": card["grade"],
                            "source_observation_ids": [],
                            "source_observations": [],
                        },
                    )
                    _append_unique(
                        evidence_item["source_observation_ids"], observation.observation_id
                    )
                    _append_unique(evidence_item["source_observations"], source)
                if missing:
                    unmatched.append(
                        {
                            "observation_id": observation.observation_id,
                            "metric_code": observation.metric_code,
                            "metric_label": METRIC_LABELS[observation.metric_code],
                            "condition_codes": missing,
                            "condition_names": [CONDITION_BY_CODE[code].name for code in missing],
                            "reason": "no_published_knowledge_card",
                        }
                    )

            findings = sorted(
                findings_by_condition.values(),
                key=lambda item: (
                    {"emergency": 0, "urgent": 1, "soon": 2, "routine": 3}[item["urgency"]],
                    -int(item["abnormality_severity"]),
                    _finding_evidence_rank(item),
                    item["department"],
                    item["condition_code"],
                ),
            )
            result_findings = []
            for item in findings:
                evidence_items = sorted(
                    item.pop("_evidence_items").values(),
                    key=lambda evidence_item: evidence_item["metric_code"],
                )
                item["evidence_items"] = evidence_items
                item["evidence_strength"] = _evidence_strength_summary(
                    evidence_item["evidence_strength"] for evidence_item in evidence_items
                )
                item["sorting"] = {
                    "urgency": item["urgency"],
                    "abnormality_severity": item["abnormality_severity"],
                    "evidence_strength": item["evidence_strength"],
                    "needs_recheck": item["needs_recheck"],
                    "department": item["department"],
                    "epidemiology_background": item["epidemiology_background"],
                }
                recommendations = recommend(
                    str(item["condition_code"]),
                    item["source_observations"],  # type: ignore[arg-type]
                    products=published_products,
                    urgency=str(item["urgency"]),
                    abnormality_severity=int(item["abnormality_severity"]),
                )
                item["recommendations"] = [
                    recommendation.as_dict() for recommendation in recommendations
                ]
                item["recommendation_message"] = recommendation_message(recommendations)
                item["product_status"] = (
                    "available" if recommendations else "not_implemented"
                )
                result_findings.append(item)
            result = {
                "schema_version": "3",
                "sorting_version": ASSESSMENT_SORTING_VERSION,
                "correlation_id": correlation_id,
                "findings": result_findings,
                "unmatched": unmatched,
                "skipped": skipped,
                "message": "" if result_findings else "暂无已审核内容",
            }
            result["patient_reply"] = _patient_reply(result_findings, unmatched)
            connection.execute(
                """
                INSERT INTO audit_events(
                    entity_type, entity_id, action, actor, detail_json, created_at
                )
                VALUES ('evidence_request', ?, 'published_card_match', ?, ?, ?)
                """,
                (
                    correlation_id,
                    actor,
                    json.dumps(
                        {
                            "observation_count": len(observations),
                            "abnormal_count": abnormal_count,
                            "finding_count": len(result_findings),
                            "unmatched_count": len(unmatched),
                            "skipped_count": len(skipped),
                            "recommendation_count": sum(
                                len(finding["recommendations"])
                                for finding in result_findings
                            ),
                            "metric_codes": sorted({item.metric_code for item in observations}),
                            "card_ids": sorted(
                                {
                                    evidence_item["card"]["id"]
                                    for finding in result_findings
                                    for evidence_item in finding["evidence_items"]
                                }
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    datetime.now(UTC).isoformat(),
                ),
            )
        if schema_version == "2":
            return _legacy_v2_response(result)
        return result


def _published_cards(connection) -> dict[tuple[str, str], dict[str, object]]:
    rows = connection.execute(
        """
        SELECT kc.id, kc.condition_code, kc.version, kc.grade, kc.published_at,
            kc.evidence_profile_id, ep.scope_key, kc.patient_visible_body,
            cc.claim_id, cc.evidence_text AS card_evidence, cc.locator,
            cl.candidate_text, cl.paper_id, p.title AS paper_title, p.doi
        FROM knowledge_cards kc
        JOIN evidence_profiles ep ON ep.id = kc.evidence_profile_id
        LEFT JOIN card_claims cc ON cc.card_id = kc.id
        LEFT JOIN claims cl ON cl.id = cc.claim_id
        LEFT JOIN papers p ON p.id = cl.paper_id
        WHERE kc.status = 'published' AND kc.grade IN ('high', 'moderate', 'low')
        ORDER BY kc.published_at DESC, kc.version DESC
        """
    ).fetchall()
    cards: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        scope_key = str(row["scope_key"] or "").strip()
        if not scope_key:
            continue
        card = cards.setdefault(
            (row["condition_code"], scope_key),
            {
                "id": row["id"],
                "condition_code": row["condition_code"],
                "scope_key": scope_key,
                "version": row["version"],
                "status": "published",
                "grade": row["grade"],
                "published_at": row["published_at"],
                "evidence_profile_id": row["evidence_profile_id"],
                "patient_visible_body": row["patient_visible_body"],
                "sources": [],
                **card_capabilities(str(row["grade"])),
            },
        )
        if row["claim_id"]:
            source = {
                "claim_id": row["claim_id"],
                "paper_id": row["paper_id"],
                "paper_title": row["paper_title"],
                "doi": row["doi"],
                "evidence": row["card_evidence"] or row["candidate_text"] or "",
                "locator": row["locator"] or "",
            }
            if source not in card["sources"]:
                card["sources"].append(source)  # type: ignore[union-attr]
    return cards


def _legacy_v2_response(result: dict[str, object]) -> dict[str, object]:
    """Flatten the v3 condition groups for clients that still speak v2."""

    v3_findings = result["findings"]
    v2_findings: list[dict[str, object]] = []
    matched_observation_ids: set[str] = set()
    for finding in v3_findings:  # type: ignore[union-attr]
        for item in finding["evidence_items"]:  # type: ignore[index]
            source_ids = list(item["source_observation_ids"])  # type: ignore[index]
            matched_observation_ids.update(source_ids)
            evidence_strength = str(item["evidence_strength"])
            sorting = {
                "urgency": finding["urgency"],
                "abnormality_severity": finding["abnormality_severity"],
                "evidence_strength": evidence_strength,
                "needs_recheck": finding["needs_recheck"],
                "department": finding["department"],
                "epidemiology_background": finding["epidemiology_background"],
            }
            v2_findings.append(
                {
                    "condition_code": finding["condition_code"],
                    "condition_name": finding["condition_name"],
                    "card": item["card"],
                    "source_observation_ids": source_ids,
                    "urgency": finding["urgency"],
                    "abnormality_severity": finding["abnormality_severity"],
                    "evidence_strength": evidence_strength,
                    "needs_recheck": finding["needs_recheck"],
                    "department": finding["department"],
                    "recheck_direction": finding["recheck_direction"],
                    "epidemiology_background": finding["epidemiology_background"],
                    "source_observations": item["source_observations"],
                    "sorting": sorting,
                    "content_layer": item["card"]["content_layer"],
                    "action_status": item["card"]["action_status"],
                    "action_message": item["card"]["action_message"],
                    "product_status": finding["product_status"],
                    "recommendations": finding["recommendations"],
                    "recommendation_message": finding["recommendation_message"],
                }
            )

    unmatched = [
        {key: value for key, value in item.items() if key != "condition_names"}
        for item in result["unmatched"]  # type: ignore[union-attr]
        if item["observation_id"] not in matched_observation_ids  # type: ignore[index]
    ]
    patient_findings = []
    for finding in v2_findings:
        card = finding["card"]
        patient_findings.append(
            {
                "condition_code": finding["condition_code"],
                "condition_name": finding["condition_name"],
                "urgency": finding["urgency"],
                "abnormality_severity": finding["abnormality_severity"],
                "evidence_strength": finding["evidence_strength"],
                "needs_recheck": finding["needs_recheck"],
                "department": finding["department"],
                "recheck_direction": finding["recheck_direction"],
                "card_id": card["id"],
                "card_version": card["version"],
                "evidence_profile_id": card["evidence_profile_id"],
                "patient_visible_body": card["patient_visible_body"],
                "sources": card["sources"],
                "source_observation_ids": finding["source_observation_ids"],
                "source_observations": finding["source_observations"],
                "content_layer": finding["content_layer"],
                "action_status": finding["action_status"],
                "action_message": finding["action_message"],
                "product_status": finding["product_status"],
                "recommendations": finding["recommendations"],
                "recommendation_message": finding["recommendation_message"],
            }
        )
    return {
        "schema_version": "2",
        "sorting_version": result["sorting_version"],
        "correlation_id": result["correlation_id"],
        "findings": v2_findings,
        "unmatched": unmatched,
        "skipped": result["skipped"],
        "message": result["message"],
        "patient_reply": {
            "title": "体检报告解读与健康风险提示",
            "summary": (
                f"根据已确认的报告指标，发现 {len(patient_findings)} 个有正式知识卡支持的健康问题。"
                if patient_findings
                else (
                    "发现异常指标，但当前没有对应的已审核知识卡。"
                    if unmatched
                    else "当前没有发现可由已发布知识卡支持的异常指标。"
                )
            ),
            "findings": patient_findings,
            "unmatched_count": len(unmatched),
            "disclaimer": "本提示仅基于已确认指标和已发布知识卡，不构成诊断或治疗建议。",
        },
    }


def _validate_observation(observation: EvidenceMatchObservation) -> None:
    if observation.metric_code not in METRIC_CODES:
        raise ValueError(f"unknown metric_code: {observation.metric_code}")
    if not math.isfinite(observation.value) or not evidence_contains_value(
        observation.evidence_text, observation.value
    ):
        raise ValueError("confirmed value lacks source evidence")
    bounds = tuple(
        bound
        for bound in (observation.reference_low, observation.reference_high)
        if bound is not None
    )
    if any(not math.isfinite(bound) for bound in bounds) or (
        len(bounds) == 2 and bounds[0] > bounds[1]
    ):
        raise ValueError("confirmed reference range is invalid")
    if any(not evidence_contains_value(observation.evidence_text, bound) for bound in bounds):
        raise ValueError("confirmed reference range lacks source evidence")


def _is_abnormal(observation: EvidenceMatchObservation) -> bool:
    return (
        observation.reference_low is not None and observation.value < observation.reference_low
    ) or (observation.reference_high is not None and observation.value > observation.reference_high)


def _source_observation(observation: EvidenceMatchObservation) -> dict[str, object]:
    source: dict[str, object] = {
        "observation_id": observation.observation_id,
        "metric_code": observation.metric_code,
        "value": observation.value,
        "unit": observation.unit,
        "reference_low": observation.reference_low,
        "reference_high": observation.reference_high,
        "evidence_text": observation.evidence_text,
        "source_file_index": observation.source_file_index,
        "source_page": observation.source_page,
        "source_id": observation.source_id,
        "bbox_normalized": observation.bbox_normalized,
    }
    if observation.source_url:
        source["source_url"] = observation.source_url
    if observation.bbox is not None:
        source["bbox"] = observation.bbox
    return source


def _append_unique(items: list[object], value: object) -> None:
    if value not in items:
        items.append(value)


def _evidence_strength_summary(grades: Iterable[str]) -> str:
    unique = set(grades)
    return next(iter(unique)) if len(unique) == 1 else "mixed"


def _finding_evidence_rank(finding: dict[str, object]) -> int:
    items = finding.get("_evidence_items", {}).values()
    return max(EVIDENCE_RANK[item["evidence_strength"]] for item in items)


def _patient_reply(
    findings: list[dict[str, object]], unmatched: list[dict[str, object]]
) -> dict[str, object]:
    visible_findings = []
    for finding in findings:
        visible = {
            "condition_code": finding["condition_code"],
            "condition_name": finding["condition_name"],
            "urgency": finding["urgency"],
            "abnormality_severity": finding["abnormality_severity"],
            "evidence_strength": finding["evidence_strength"],
            "needs_recheck": finding["needs_recheck"],
            "department": finding["department"],
            "recheck_direction": finding["recheck_direction"],
            "source_observation_ids": finding["source_observation_ids"],
            "source_observations": finding["source_observations"],
            "content_layer": finding["content_layer"],
            "action_status": finding["action_status"],
            "action_message": finding["action_message"],
            "product_status": finding["product_status"],
            "recommendations": finding["recommendations"],
            "recommendation_message": finding["recommendation_message"],
            "evidence_items": finding["evidence_items"],
        }
        visible_findings.append(visible)
    if visible_findings:
        metric_count = len(
            {
                observation_id
                for finding in visible_findings
                for observation_id in finding["source_observation_ids"]
            }
        )
        summary = (
            f"根据已确认的报告指标，发现 {len(visible_findings)} 个可能相关健康问题，"
            f"涉及 {metric_count} 个异常指标。"
        )
        if unmatched:
            summary += f"另有 {len(unmatched)} 条指标与健康问题关联暂无已审核知识卡。"
    elif unmatched:
        summary = "发现异常指标，但当前没有对应的已审核知识卡。"
    else:
        summary = "当前没有发现可由已发布知识卡支持的异常指标。"
    return {
        "title": "体检报告解读与健康风险提示",
        "summary": summary,
        "findings": visible_findings,
        "unmatched_count": len(unmatched),
        "disclaimer": "本提示仅基于已确认指标和已发布知识卡，不构成诊断或治疗建议。",
    }
