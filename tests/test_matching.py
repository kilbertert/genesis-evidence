"""Direct unit coverage for the EvidenceMatcher deep module.

The store methods keep their byte-for-byte response contracts; this file
exercises the matcher interface itself with in-memory card adapters so the
shared reference-range gate, scope resolver, and per-path projection seams
can be tested in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from genesis_evidence.core.conditions import CONDITION_BY_CODE
from genesis_evidence.core.matching import (
    CONDITIONS_BY_METRIC,
    CardAdapter,
    CardScopeResolver,
    EvidenceMatcher,
    MatchObservation,
    ObservationInput,
    _is_v2_unmatched,
    _is_v3_unmatched,
    is_abnormal,
    patient_reply_v2,
    patient_reply_v3,
    project_observation,
    validate_metric_code,
    validate_number,
    validate_observation,
)
from genesis_evidence.core.metrics import METRIC_LABELS


def _card(card_id: str, *, scope_key: str, grade: str = "moderate") -> dict[str, object]:
    return {
        "id": card_id,
        "condition_code": "COND_PREDIABETES",
        "scope_key": scope_key,
        "grade": grade,
        "content_layer": "context_only",
        "action_status": "not_available",
        "action_message": "msg",
        "product_status": "not_implemented",
    }


def _adapter(cards: dict[tuple[str, str], dict[str, object]]) -> CardAdapter:
    return CardAdapter(
        condition_codes_for_metric=lambda metric_code: CONDITIONS_BY_METRIC[metric_code],
        lookup=lambda condition_code, scope_key: cards.get((condition_code, scope_key)),
    )


def _entry(
    observation_id: str = "m1",
    metric_code: str = "fasting_glucose",
    value: float = 6.8,
    low: float | None = 3.9,
    high: float | None = 6.1,
    *,
    evidence_text: str = "空腹血糖 6.8 mmol/L 3.9-6.1 H",
) -> MatchObservation:
    return MatchObservation(
        input=ObservationInput(
            observation_id=observation_id,
            metric_code=metric_code,
            value=value,
            unit="mmol/L",
            reference_low=low,
            reference_high=high,
            evidence_text=evidence_text,
        )
    )


def test_loose_resolver_keeps_card_scope_key_in_evidence_item() -> None:
    cards = {
        ("COND_PREDIABETES", "metric:fasting_glucose"): _card(
            "c1", scope_key="metric:fasting_glucose"
        )
    }

    def produce(fc, condition, card, entry, scope_key):
        finding = fc.setdefault(condition.code, {"_evidence_items": {}})
        finding["_evidence_items"].setdefault(
            scope_key,
            {"metric_code": entry.input.metric_code, "card": card},
        )

    result = EvidenceMatcher.match_published_cards(
        [_entry()],
        adapter=_adapter(cards),
        resolver=CardScopeResolver(strict=False),
        produce_finding=produce,
        collect_unmatched=_is_v3_unmatched,
        validate=validate_observation,
    )
    assert "COND_PREDIABETES" in result.findings
    assert list(result.findings["COND_PREDIABETES"]["_evidence_items"].keys()) == [
        "metric:fasting_glucose"
    ]
    assert result.card_ids == ["c1"]


def test_strict_resolver_rejects_non_metric_scopes() -> None:
    cards = {("COND_PREDIABETES", "anything-else"): _card("c1", scope_key="anything-else")}
    adapter = CardAdapter(
        condition_codes_for_metric=lambda metric_code: CONDITIONS_BY_METRIC[metric_code],
        lookup=lambda condition_code, scope_key: cards.get((condition_code, "anything-else")),
    )

    def produce(fc, condition, card, entry, scope_key):
        pytest.fail(f"strict resolver should not produce, got {scope_key}")

    result = EvidenceMatcher.match_published_cards(
        [_entry()],
        adapter=adapter,
        resolver=CardScopeResolver(strict=True),
        produce_finding=produce,
        collect_unmatched=_is_v2_unmatched,
        validate=validate_observation,
    )
    assert result.findings == {}
    missing = [condition.code for condition in CONDITIONS_BY_METRIC["fasting_glucose"]]
    assert result.unmatched == [
        {
            "observation_id": "m1",
            "metric_code": "fasting_glucose",
            "metric_label": METRIC_LABELS["fasting_glucose"],
            "condition_codes": missing,
            "reason": "no_published_knowledge_card",
        }
    ]


def test_v3_unmatched_emits_all_unsatisfied_condition_codes() -> None:
    cards = {}  # no published cards at all
    result = EvidenceMatcher.match_published_cards(
        [_entry()],
        adapter=_adapter(cards),
        resolver=CardScopeResolver(strict=False),
        produce_finding=lambda *args: None,
        collect_unmatched=_is_v3_unmatched,
        validate=validate_observation,
    )
    assert len(result.unmatched) == 1
    entry = result.unmatched[0]
    assert entry["observation_id"] == "m1"
    assert "COND_PREDIABETES" in entry["condition_codes"]
    assert entry["condition_names"] == [CONDITION_BY_CODE[c].name for c in entry["condition_codes"]]


def test_v2_unmatched_only_fires_when_no_condition_is_matched() -> None:
    cards = {
        ("COND_PREDIABETES", "metric:fasting_glucose"): _card(
            "c1", scope_key="metric:fasting_glucose"
        )
    }

    # partial coverage: only one of the conditions has a card, the metric still resolves
    # with the strict resolver because we publish a card for the strict path.
    # We need a metric whose CONDITIONS_BY_METRIC has multiple conditions and
    # we only publish for ONE of them, leaving the other missing, but match a
    # published one for the strict resolver. fasting_glucose maps to 2 conditions;
    # by publishing only one we get partial coverage. With strict resolver, the
    # published condition will be matched, and the missing one is still "missing"
    # but the v2 collect_unmatched drops it.
    conditions = CONDITIONS_BY_METRIC["fasting_glucose"]
    assert len(conditions) >= 2  # precondition for this test

    result = EvidenceMatcher.match_published_cards(
        [_entry()],
        adapter=_adapter(cards),
        resolver=CardScopeResolver(strict=True),
        produce_finding=lambda *args: None,
        collect_unmatched=_is_v2_unmatched,
        validate=validate_observation,
    )
    assert result.unmatched == []  # matched -> unmatched suppressed


def test_validate_number_reproduces_v2_error_messages_byte_for_byte() -> None:
    with pytest.raises(ValueError, match="^confirmed value lacks source evidence$"):
        validate_number("", None, 3.9, 6.1, prefix="confirmed ")
    with pytest.raises(ValueError, match="^confirmed value lacks source evidence$"):
        validate_number("no number here", 6.8, 3.9, 6.1, prefix="confirmed ")
    with pytest.raises(ValueError, match="^confirmed reference range is invalid$"):
        validate_number("6.8", 6.8, 7.0, 6.0, prefix="confirmed ")
    with pytest.raises(ValueError, match="^confirmed reference range lacks source evidence$"):
        validate_number("6.8 3.9", 6.8, 3.9, 99.0, prefix="confirmed ")
    validate_number("5.0", 5.0, 5.0, 5.0, prefix="confirmed ")


@pytest.mark.parametrize(
    ("evidence_text", "low", "high"),
    [
        ("6.8 6.1", 3.9, 6.1),
        ("6.8 3.9", 3.9, 6.1),
    ],
)
def test_validate_observation_checks_both_source_bearing_bounds(
    evidence_text: str, low: float, high: float
) -> None:
    with pytest.raises(ValueError, match="^confirmed reference range lacks source evidence$"):
        validate_observation(_entry(low=low, high=high, evidence_text=evidence_text).input)


def test_validate_metric_code_raises_v2_message() -> None:
    with pytest.raises(ValueError, match="^unknown metric_code: bogus$"):
        validate_metric_code("bogus")


def test_is_abnormal_matches_v2_external_kernel() -> None:
    assert is_abnormal(7.0, 3.9, 6.1) is True
    assert is_abnormal(3.0, 3.9, 6.1) is True
    assert is_abnormal(5.0, 3.9, 6.1) is False
    assert is_abnormal(5.0, None, 6.1) is False
    assert is_abnormal(7.0, None, 6.1) is True
    assert is_abnormal(3.0, 3.9, None) is True
    assert is_abnormal(7.0, 3.9, None) is False
    assert is_abnormal(3.9, 3.9, 6.1) is False
    assert is_abnormal(6.1, 3.9, 6.1) is False


def test_patient_reply_v2_uses_flat_card_shape() -> None:
    finding = {
        "condition_code": "COND_PREDIABETES",
        "condition_name": "糖尿病前期 / 糖代谢异常",
        "urgency": "routine",
        "abnormality_severity": 1,
        "evidence_strength": "moderate",
        "needs_recheck": True,
        "department": "内分泌科",
        "recheck_direction": "复查空腹血糖与糖化血红蛋白",
        "card": {
            "id": "card-1",
            "version": "1.0.0",
            "evidence_profile_id": "profile-1",
            "patient_visible_body": "body",
            "sources": [],
        },
        "source_observation_ids": ["m1"],
        "source_observations": [],
        "content_layer": "context_only",
        "action_status": "not_available",
        "action_message": "msg",
        "product_status": "not_implemented",
    }
    assert patient_reply_v2([finding], []) == {
        "title": "体检报告解读与健康风险提示",
        "summary": "根据已确认的报告指标，发现 1 个有正式知识卡支持的健康问题。",
        "findings": [
            {
                "condition_code": "COND_PREDIABETES",
                "condition_name": "糖尿病前期 / 糖代谢异常",
                "urgency": "routine",
                "abnormality_severity": 1,
                "evidence_strength": "moderate",
                "needs_recheck": True,
                "department": "内分泌科",
                "recheck_direction": "复查空腹血糖与糖化血红蛋白",
                "card_id": "card-1",
                "card_version": "1.0.0",
                "evidence_profile_id": "profile-1",
                "patient_visible_body": "body",
                "sources": [],
                "source_observation_ids": ["m1"],
                "source_observations": [],
                "content_layer": "context_only",
                "action_status": "not_available",
                "action_message": "msg",
                "product_status": "not_implemented",
            }
        ],
        "unmatched_count": 0,
        "disclaimer": "本提示仅基于已确认指标和已发布知识卡，不构成诊断或治疗建议。",
    }
    assert patient_reply_v2([], [{}])["summary"] == "发现异常指标，但当前没有对应的已审核知识卡。"
    assert patient_reply_v2([], [])["summary"] == "当前没有发现可由已发布知识卡支持的异常指标。"


def test_patient_reply_v3_carries_evidence_items_per_finding() -> None:
    finding = {
        "condition_code": "COND_PREDIABETES",
        "condition_name": "糖尿病前期 / 糖代谢异常",
        "urgency": "routine",
        "abnormality_severity": 1,
        "evidence_strength": "moderate",
        "needs_recheck": True,
        "department": "内分泌科",
        "recheck_direction": "复查空腹血糖与糖化血红蛋白",
        "source_observation_ids": ["m1"],
        "source_observations": [],
        "content_layer": "context_only",
        "action_status": "not_available",
        "action_message": "msg",
        "product_status": "not_implemented",
        "evidence_items": [
            {
                "metric_code": "fasting_glucose",
                "card": {"id": "card-1"},
            }
        ],
    }
    reply = patient_reply_v3([finding], [])
    visible = reply["findings"][0]
    assert "evidence_items" in visible
    assert visible["evidence_items"][0]["card"]["id"] == "card-1"
    assert patient_reply_v3([], [{}])["summary"] == "发现异常指标，但当前没有对应的已审核知识卡。"
    assert patient_reply_v3([], [])["summary"] == "当前没有发现可由已发布知识卡支持的异常指标。"


def test_project_observation_preserves_all_source_fields() -> None:
    observation = SimpleNamespace(
        observation_id="m1",
        metric_code="fasting_glucose",
        value=6.8,
        unit="mmol/L",
        reference_low=3.9,
        reference_high=6.1,
        evidence_text="source text",
        source_file_index=2,
        source_page=3,
        source_id="source-1",
        source_url="https://example.test/report",
        bbox=[1.0, 2.0, 3.0, 4.0],
        bbox_normalized=[0.1, 0.2, 0.3, 0.4],
    )

    projected = project_observation(observation)

    assert projected.input == ObservationInput(
        observation_id="m1",
        metric_code="fasting_glucose",
        value=6.8,
        unit="mmol/L",
        reference_low=3.9,
        reference_high=6.1,
        evidence_text="source text",
        source_file_index=2,
        source_page=3,
        source_id="source-1",
        source_url="https://example.test/report",
        bbox=[1.0, 2.0, 3.0, 4.0],
        bbox_normalized=[0.1, 0.2, 0.3, 0.4],
    )
    assert projected.source == {
        "observation_id": "m1",
        "metric_code": "fasting_glucose",
        "value": 6.8,
        "unit": "mmol/L",
        "reference_low": 3.9,
        "reference_high": 6.1,
        "evidence_text": "source text",
        "source_file_index": 2,
        "source_page": 3,
        "source_id": "source-1",
        "bbox_normalized": [0.1, 0.2, 0.3, 0.4],
        "source_url": "https://example.test/report",
        "bbox": [1.0, 2.0, 3.0, 4.0],
    }


def test_evidence_strength_summary_mixed_grade_set() -> None:
    # Two grades on the same condition -> "mixed"
    cards = {
        ("COND_PREDIABETES", "metric:fasting_glucose"): _card(
            "c1", scope_key="metric:fasting_glucose", grade="moderate"
        )
    }

    def produce(fc, condition, card, entry, scope_key):
        fc.setdefault(
            condition.code,
            {
                "_evidence_items": {
                    "metric:fasting_glucose": {"evidence_strength": "moderate"},
                    "metric:hba1c": {"evidence_strength": "low"},
                }
            },
        )

    result = EvidenceMatcher.match_published_cards(
        [_entry()],
        adapter=_adapter(cards),
        resolver=CardScopeResolver(strict=False),
        produce_finding=produce,
        collect_unmatched=_is_v3_unmatched,
        validate=validate_observation,
    )
    from genesis_evidence.core.matching import evidence_strength_summary

    assert (
        evidence_strength_summary(
            item["evidence_strength"]
            for item in result.findings["COND_PREDIABETES"]["_evidence_items"].values()
        )
        == "mixed"
    )


def test_metric_codes_include_skipped_observations() -> None:
    result = EvidenceMatcher.match_published_cards(
        [
            _entry(value=5.0),
            _entry("m2", "uric_acid", 300.0, 200.0, 420.0),
        ],
        adapter=_adapter({}),
        resolver=CardScopeResolver(strict=False),
        produce_finding=lambda *args: None,
        collect_unmatched=_is_v3_unmatched,
    )

    assert result.metric_codes == ["fasting_glucose", "uric_acid"]


def test_missing_reference_range_is_skipped() -> None:
    result = EvidenceMatcher.match_published_cards(
        [_entry(low=None, high=None)],
        adapter=_adapter({}),
        resolver=CardScopeResolver(strict=False),
        produce_finding=lambda *args: None,
        collect_unmatched=_is_v3_unmatched,
        validate=validate_observation,
    )

    assert result.skipped == [{"observation_id": "m1", "reason": "missing_reference_range"}]
    assert result.abnormal_count == 0


def test_skipped_observation_does_not_stop_later_abnormal_matches() -> None:
    result = EvidenceMatcher.match_published_cards(
        [
            _entry("missing", low=None, high=None),
            _entry("high", value=7.0),
            _entry("low", value=3.0),
        ],
        adapter=_adapter({}),
        resolver=CardScopeResolver(strict=False),
        produce_finding=lambda *args: None,
        collect_unmatched=_is_v3_unmatched,
    )

    assert result.abnormal_count == 2
    assert [item["observation_id"] for item in result.unmatched] == ["high", "low"]
