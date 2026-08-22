"""Read-only published-evidence query store for Health-Flow."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime

from ..conditions import CONDITIONS
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
    ) -> dict[str, object]:
        with self.database.transaction() as connection:
            cards = _published_cards(connection)
            findings_by_card: dict[tuple[str, str], dict[str, object]] = {}
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
                matched = False
                source = _source_observation(observation)
                for condition in CONDITIONS_BY_METRIC[observation.metric_code]:
                    card = cards.get((condition.code, f"metric:{observation.metric_code}"))
                    if card is None:
                        missing.append(condition.code)
                        continue
                    matched = True
                    scope_key = str(card["scope_key"])
                    finding = findings_by_card.setdefault(
                        (condition.code, scope_key),
                        {
                            "condition_code": condition.code,
                            "condition_name": condition.name,
                            "card": card,
                            "source_observation_ids": [],
                            "urgency": "routine",
                            "abnormality_severity": 1,
                            "evidence_strength": card["grade"],
                            "needs_recheck": True,
                            "department": condition.department,
                            "recheck_direction": condition.recheck_direction,
                            "epidemiology_background": "",
                            "source_observations": [],
                            "content_layer": card["content_layer"],
                            "action_status": card["action_status"],
                            "action_message": card["action_message"],
                            "product_status": card["product_status"],
                        },
                    )
                    finding["source_observation_ids"].append(observation.observation_id)  # type: ignore[union-attr]
                    finding["source_observations"].append(source)  # type: ignore[union-attr]
                if missing and not matched:
                    unmatched.append(
                        {
                            "observation_id": observation.observation_id,
                            "metric_code": observation.metric_code,
                            "metric_label": METRIC_LABELS[observation.metric_code],
                            "condition_codes": missing,
                            "reason": "no_published_knowledge_card",
                        }
                    )

            findings = sorted(
                findings_by_card.values(),
                key=lambda item: (
                    {"emergency": 0, "urgent": 1, "soon": 2, "routine": 3}[item["urgency"]],
                    -int(item["abnormality_severity"]),
                    EVIDENCE_RANK[item["evidence_strength"]],
                    item["department"],
                    item["card"]["scope_key"],
                ),
            )
            result_findings = []
            for item in findings:
                card = item.pop("card")
                item["sorting"] = {
                    "urgency": item["urgency"],
                    "abnormality_severity": item["abnormality_severity"],
                    "evidence_strength": item["evidence_strength"],
                    "needs_recheck": item["needs_recheck"],
                    "department": item["department"],
                    "epidemiology_background": item["epidemiology_background"],
                }
                result_findings.append({**item, "card": card})
            result = {
                "schema_version": "2",
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
                            "metric_codes": sorted({item.metric_code for item in observations}),
                            "card_ids": sorted({item["card"]["id"] for item in result_findings}),
                        },
                        ensure_ascii=False,
                    ),
                    datetime.now(UTC).isoformat(),
                ),
            )
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


def _patient_reply(
    findings: list[dict[str, object]], unmatched: list[dict[str, object]]
) -> dict[str, object]:
    visible_findings = []
    for finding in findings:
        card = finding["card"]
        visible_findings.append(
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
            }
        )
    if visible_findings:
        summary = (
            f"根据已确认的报告指标，发现 {len(visible_findings)} 个有正式知识卡支持的健康问题。"
        )
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
