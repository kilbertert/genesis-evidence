"""Deep module for the deterministic confirmed-observation -> published-card chain.

Owns the shared registries, the reference-range gate, the scope resolver, and
the per-path projection seams (finding shape + unmatched policy + optional
observation validation). The three store entry points
(`EvidenceStore.match_published_cards`, `ReportStore.match_published_cards`,
`ReportStore.assess`) become thin callers that supply the path-specific
card source, scope strictness, and result projection.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .conditions import CONDITION_BY_CODE, CONDITIONS
from .metrics import METRIC_LABELS, evidence_contains_value

METRIC_CODES = frozenset(metric for condition in CONDITIONS for metric in condition.metrics)
CONDITIONS_BY_METRIC = {
    metric: tuple(condition for condition in CONDITIONS if metric in condition.metrics)
    for metric in METRIC_CODES
}
EVIDENCE_RANK = {"high": 0, "moderate": 1, "low": 2, "very_low": 3}
ASSESSMENT_SORTING_VERSION = "published-card-reference-range-v1"


def validate_metric_code(metric_code: str) -> None:
    if metric_code not in METRIC_CODES:
        raise ValueError(f"unknown metric_code: {metric_code}")


def validate_number(
    evidence: str,
    value: float | None,
    reference_low: float | None,
    reference_high: float | None,
    *,
    prefix: str,
) -> None:
    if value is None or not math.isfinite(value) or not evidence_contains_value(evidence, value):
        raise ValueError(f"{prefix}value lacks source evidence")
    bounds = tuple(bound for bound in (reference_low, reference_high) if bound is not None)
    if any(not math.isfinite(bound) for bound in bounds) or (
        len(bounds) == 2 and bounds[0] > bounds[1]
    ):
        raise ValueError(f"{prefix}reference range is invalid")
    if any(not evidence_contains_value(evidence, bound) for bound in bounds):
        raise ValueError(f"{prefix}reference range lacks source evidence")


def validate_observation(observation) -> None:
    """Validate an `EvidenceMatchObservation` (or duck-typed equivalent) against source text.

    Reproduces the v2/v3 error messages byte-for-byte: `unknown metric_code: ...`,
    `confirmed value lacks source evidence`, `confirmed reference range is invalid`,
    `confirmed reference range lacks source evidence`.
    """

    validate_metric_code(observation.metric_code)
    validate_number(
        observation.evidence_text,
        observation.value,
        observation.reference_low,
        observation.reference_high,
        prefix="confirmed ",
    )


def is_abnormal(value: float, low: float | None, high: float | None) -> bool:
    return (low is not None and value < low) or (high is not None and value > high)


def source_observation(observation) -> dict[str, object]:
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


def append_unique(items: list[object], value: object) -> None:
    if value not in items:
        items.append(value)


def evidence_strength_summary(grades: Iterable[str]) -> str:
    unique = set(grades)
    return next(iter(unique)) if len(unique) == 1 else "mixed"


def finding_evidence_rank(finding: dict[str, object]) -> int:
    items = finding.get("_evidence_items", {}).values()
    return max(EVIDENCE_RANK[item["evidence_strength"]] for item in items)


def patient_reply_v3(
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


def patient_reply_v2(
    findings: list[dict[str, object]], unmatched: list[dict[str, object]]
) -> dict[str, object]:
    """Build a patient-facing envelope from stored card fields only."""

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


@dataclass(frozen=True, slots=True)
class ObservationInput:
    observation_id: str
    metric_code: str
    value: float
    unit: str = ""
    reference_low: float | None = None
    reference_high: float | None = None
    evidence_text: str = ""
    source_file_index: int = 1
    source_page: int = 1
    source_id: str | None = None
    source_url: str | None = None
    bbox: list[float] | None = None
    bbox_normalized: list[float] | None = None


@dataclass(frozen=True, slots=True)
class MatchObservation:
    input: ObservationInput
    source: dict[str, object] | None = None


def project_observation(observation) -> MatchObservation:
    return MatchObservation(
        input=ObservationInput(
            observation_id=observation.observation_id,
            metric_code=observation.metric_code,
            value=observation.value,
            unit=observation.unit,
            reference_low=observation.reference_low,
            reference_high=observation.reference_high,
            evidence_text=observation.evidence_text,
            source_file_index=observation.source_file_index,
            source_page=observation.source_page,
            source_id=observation.source_id,
            source_url=observation.source_url,
            bbox=observation.bbox,
            bbox_normalized=observation.bbox_normalized,
        ),
        source=source_observation(observation),
    )


@dataclass(frozen=True, slots=True)
class CardScopeResolver:
    strict: bool

    def scope_key(self, metric_code: str, card_scope_key: str) -> str | None:
        if self.strict:
            expected = f"metric:{metric_code}"
            return expected if card_scope_key == expected else None
        return card_scope_key


@dataclass(frozen=True, slots=True)
class CardAdapter:
    condition_codes_for_metric: Callable[[str], Iterable[object]]
    lookup: Callable[[str, str], dict[str, object] | None]


@dataclass
class MatchResult:
    findings: dict[str, dict[str, object]] = field(default_factory=dict)
    unmatched: list[dict[str, object]] = field(default_factory=list)
    skipped: list[dict[str, object]] = field(default_factory=list)
    abnormal_count: int = 0
    card_ids: list[str] = field(default_factory=list)
    metric_codes: list[str] = field(default_factory=list)


def _is_v3_unmatched(
    entry: MatchObservation, missing: list[str], matched: bool
) -> dict[str, object] | None:
    if not missing:
        return None
    inp = entry.input
    return {
        "observation_id": inp.observation_id,
        "metric_code": inp.metric_code,
        "metric_label": METRIC_LABELS[inp.metric_code],
        "condition_codes": missing,
        "condition_names": [CONDITION_BY_CODE[code].name for code in missing],
        "reason": "no_published_knowledge_card",
    }


def _is_v2_unmatched(
    entry: MatchObservation, missing: list[str], matched: bool
) -> dict[str, object] | None:
    if not (missing and not matched):
        return None
    inp = entry.input
    return {
        "observation_id": inp.observation_id,
        "metric_code": inp.metric_code,
        "metric_label": METRIC_LABELS.get(inp.metric_code, inp.metric_code),
        "condition_codes": missing,
        "reason": "no_published_knowledge_card",
    }


class EvidenceMatcher:
    """Deterministic observation-to-card chain shared by the three store entry points."""

    @staticmethod
    def match_published_cards(
        observations: Iterable[MatchObservation],
        *,
        adapter: CardAdapter,
        resolver: CardScopeResolver,
        produce_finding: Callable[
            [
                dict[str, dict[str, object]],
                object,
                dict[str, object],
                MatchObservation,
                str,
            ],
            None,
        ],
        collect_unmatched: Callable[[MatchObservation, list[str], bool], dict[str, object] | None],
        validate: Callable[[ObservationInput], None] | None = None,
    ) -> MatchResult:
        result = MatchResult()
        for entry in observations:
            inp = entry.input
            result.metric_codes.append(inp.metric_code)
            if validate is not None:
                validate(inp)
            if inp.reference_low is None and inp.reference_high is None:
                result.skipped.append(
                    {
                        "observation_id": inp.observation_id,
                        "reason": "missing_reference_range",
                    }
                )
                continue
            if not is_abnormal(inp.value, inp.reference_low, inp.reference_high):
                result.skipped.append(
                    {"observation_id": inp.observation_id, "reason": "within_reference_range"}
                )
                continue
            result.abnormal_count += 1
            missing: list[str] = []
            matched = False
            for condition in adapter.condition_codes_for_metric(inp.metric_code):
                card = adapter.lookup(condition.code, f"metric:{inp.metric_code}")
                if card is None:
                    missing.append(condition.code)
                    continue
                scope = resolver.scope_key(inp.metric_code, str(card.get("scope_key", "")))
                if scope is None:
                    missing.append(condition.code)
                    continue
                matched = True
                result.card_ids.append(str(card["id"]))
                produce_finding(result.findings, condition, card, entry, scope)
            unmatched = collect_unmatched(entry, missing, matched)
            if unmatched is not None:
                result.unmatched.append(unmatched)
        result.metric_codes = sorted(set(result.metric_codes))
        result.card_ids = sorted(set(result.card_ids))
        return result
