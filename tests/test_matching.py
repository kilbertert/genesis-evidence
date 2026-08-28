"""Direct unit coverage for the EvidenceMatcher deep module.

The store methods keep their byte-for-byte response contracts; this file
exercises the matcher interface itself with in-memory card adapters so the
shared reference-range gate, scope resolver, and per-path projection seams
can be tested in isolation.
"""

from __future__ import annotations

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
    validate_metric_code,
    validate_number,
    validate_observation,
)


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
    assert len(result.unmatched) == 1


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
        validate_number("no number here", 6.8, 3.9, 6.1, prefix="confirmed ")
    with pytest.raises(ValueError, match="^confirmed reference range is invalid$"):
        validate_number("6.8", 6.8, 7.0, 6.0, prefix="confirmed ")
    with pytest.raises(ValueError, match="^confirmed reference range lacks source evidence$"):
        validate_number("6.8 3.9", 6.8, 3.9, 99.0, prefix="confirmed ")


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
    reply = patient_reply_v2([finding], [])
    visible = reply["findings"][0]
    assert "card_id" in visible
    assert "evidence_items" not in visible
    assert visible["card_id"] == "card-1"


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
