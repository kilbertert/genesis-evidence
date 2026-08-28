"""Read-only published-evidence query store for Health-Flow."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from ..contracts import EvidenceMatchObservation, card_capabilities
from ..matching import (
    ASSESSMENT_SORTING_VERSION,
    CONDITIONS_BY_METRIC,
    CardAdapter,
    CardScopeResolver,
    EvidenceMatcher,
    MatchObservation,
    MatchResult,
    _is_v3_unmatched,
    append_unique,
    evidence_strength_summary,
    finding_evidence_rank,
    patient_reply_v3,
    project_observation,
    validate_observation,
)
from ..metrics import METRIC_LABELS
from .database import Database

__all__ = ["EvidenceStore"]


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

            adapter = CardAdapter(
                condition_codes_for_metric=lambda metric_code: CONDITIONS_BY_METRIC[metric_code],
                lookup=lambda condition_code, scope_key: cards.get((condition_code, scope_key)),
            )
            resolver = CardScopeResolver(strict=False)
            entries = [project_observation(observation) for observation in observations]

            def _produce_finding(
                findings_by_condition: dict[str, dict[str, object]],
                condition,
                card: dict[str, object],
                entry: MatchObservation,
                scope_key: str,
            ) -> None:
                inp = entry.input
                source = entry.source
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
                append_unique(finding["source_observation_ids"], inp.observation_id)
                append_unique(finding["source_observations"], source)
                evidence_items = finding["_evidence_items"]
                evidence_item = evidence_items.setdefault(
                    scope_key,
                    {
                        "metric_code": inp.metric_code,
                        "metric_label": METRIC_LABELS[inp.metric_code],
                        "card": card,
                        "evidence_strength": card["grade"],
                        "source_observation_ids": [],
                        "source_observations": [],
                    },
                )
                append_unique(evidence_item["source_observation_ids"], inp.observation_id)
                append_unique(evidence_item["source_observations"], source)

            result: MatchResult = EvidenceMatcher.match_published_cards(
                entries,
                adapter=adapter,
                resolver=resolver,
                produce_finding=_produce_finding,
                collect_unmatched=_is_v3_unmatched,
                validate=validate_observation,
            )

            findings = sorted(
                result.findings.values(),
                key=lambda item: (
                    {"emergency": 0, "urgent": 1, "soon": 2, "routine": 3}[item["urgency"]],
                    -int(item["abnormality_severity"]),
                    finding_evidence_rank(item),
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
                item["evidence_strength"] = evidence_strength_summary(
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
                result_findings.append(item)
            payload = {
                "schema_version": "3",
                "sorting_version": ASSESSMENT_SORTING_VERSION,
                "correlation_id": correlation_id,
                "findings": result_findings,
                "unmatched": result.unmatched,
                "skipped": result.skipped,
                "message": "" if result_findings else "暂无已审核内容",
            }
            payload["patient_reply"] = patient_reply_v3(result_findings, result.unmatched)
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
                            "abnormal_count": result.abnormal_count,
                            "finding_count": len(result_findings),
                            "unmatched_count": len(result.unmatched),
                            "skipped_count": len(result.skipped),
                            "metric_codes": result.metric_codes,
                            "card_ids": result.card_ids,
                        },
                        ensure_ascii=False,
                    ),
                    datetime.now(UTC).isoformat(),
                ),
            )
        if schema_version == "2":
            return _legacy_v2_response(payload)
        return payload


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
                    "product_status": item["card"]["product_status"],
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
