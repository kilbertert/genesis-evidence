"""Guards for the one observation-disposition owner: `core.disposition` (#261).

*Why an observation did not become a finding, and what the patient is told about it*
was reproduced as a contract ``Literal``, two disjoint producer vocabularies, three
reader predicates, and three patient-reply builders. The copies had already come
apart, and in the safety-relevant direction: the one concept every reader tested was
spelled ``unknown_metric`` by the adapter and ``unknown_metric_code`` by every
reader, so the rider naming the unreadable rows was unreachable from the adapter's
own rows, and the report path's builder could not carry it at all.

Each guard below fails on the shape it forbids, not merely passes today.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from genesis_evidence.core.disposition import (
    ALL_IN_RANGE_SUMMARY,
    DEFERRED_SUMMARY,
    NO_CARD_SUMMARY,
    NO_PUBLISHED_CARD,
    NORMAL,
    PRODUCER_ELIGIBLE_REASONS,
    SKIP_REASONS,
    UNKNOWN_METRIC,
    UNUSABLE,
    URGENCY_LEVELS,
    URGENCY_RANK,
    UnknownDispositionValue,
    Urgency,
    disposition_class,
    empty_summary,
    is_unreadable,
    unreadable_count,
    unreadable_rider,
    urgency_rank,
    with_rider,
)
from genesis_evidence.core.matching import patient_reply_v2, patient_reply_v3

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "genesis_evidence"


def _source(relative_path: str) -> str:
    return (SOURCE_ROOT / relative_path).read_text()


#: Constants a producer may emit by name. The scan resolves these to their values,
#: so "emit the constant" and "emit the string" are the same fact to the guard —
#: otherwise the honest spelling would be the one that slips past it.
_REASON_CONSTANTS: dict[str, str] = {
    "UNKNOWN_METRIC": UNKNOWN_METRIC,
    "NO_PUBLISHED_CARD": NO_PUBLISHED_CARD,
}


def _producer_reason_literals(relative_path: str) -> set[str]:
    """The reason values a producer actually emits, read from its source.

    Two shapes count as an emission: a positional argument to the module's own
    ``_skip``/``_skip_row`` helper, and a ``"reason": "…"`` row literal. A known
    constant name is resolved to its value. Anything else is prose.
    """

    source = _source(relative_path)

    def resolve(token: str) -> str:
        return _REASON_CONSTANTS.get(token, token)

    # The trailing argument of the producer's own `_skip`/`_skip_row` helper, as a
    # string literal or a constant name.
    emitted = {
        resolve(match)
        for match in re.findall(
            r"_skip(?:_row)?\([^)]*,\s*(?:['\"]([a-z_]+)['\"]|([A-Z_][A-Z_0-9]*))\s*[,)]",
            source,
        )
        for match in match
        if match
    }
    # A `"reason": …` row literal, same two spellings.
    for literal, constant in re.findall(
        r"['\"]reason['\"]\s*:\s*(?:['\"]([a-z_]+)['\"]|([A-Z_][A-Z_0-9]*))", source
    ):
        emitted.add(resolve(literal or constant))
    return emitted


def test_the_urgency_type_is_derived_from_the_one_vocabulary() -> None:
    assert get_args(Urgency) == URGENCY_LEVELS


def test_the_contract_reason_field_derives_from_the_vocabulary() -> None:
    """A contract that re-declares the reasons is a second copy; extract and compare."""

    from genesis_evidence.core.contracts import EvidenceSkipped

    field = EvidenceSkipped.model_fields["reason"]
    assert set(get_args(field.annotation)) == set(SKIP_REASONS)


def test_no_module_restates_the_skip_reason_vocabulary_as_a_literal() -> None:
    """`Literal[...]` naming three or more reasons is a copy, in either quote style."""

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "disposition.py":
            continue
        for match in re.finditer(r"Literal\[([^\]]*)\]", path.read_text()):
            hits = [
                reason
                for reason in SKIP_REASONS
                if re.search(rf"['\"]{re.escape(reason)}['\"]", match.group(1))
            ]
            if len(hits) >= 3:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {sorted(hits)}")
    assert not offenders, "skip-reason vocabulary restated in a Literal:\n" + "\n".join(offenders)


def test_no_module_restates_the_urgency_vocabulary_as_a_literal() -> None:
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "disposition.py":
            continue
        for match in re.finditer(r"Literal\[([^\]]*)\]", path.read_text()):
            hits = [
                level
                for level in URGENCY_LEVELS
                if re.search(rf"['\"]{re.escape(level)}['\"]", match.group(1))
            ]
            if len(hits) >= 3:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {sorted(hits)}")
    assert not offenders, "urgency vocabulary restated in a Literal:\n" + "\n".join(offenders)


def test_no_module_restates_the_urgency_rank_as_a_dict_literal() -> None:
    """The rank was spelled three times, one of which raised a bare `KeyError`."""

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "disposition.py":
            continue
        text = path.read_text()
        for match in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
            span = match.group(0)
            if not re.search(r"['\"]emergency['\"]\s*:\s*\d", span):
                continue
            hits = [level for level in URGENCY_LEVELS if f"'{level}'" in span]
            if len(hits) > 1:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {sorted(hits)}")
    assert not offenders, "urgency rank restated as a dict literal:\n" + "\n".join(offenders)


def test_the_old_unknown_metric_spelling_is_gone_from_the_package() -> None:
    """`unknown_metric` was the adapter's second name for the reader's concept.

    The bare token must not survive anywhere: aliasing it would leave the reader and
    the producer testing two spellings of one idea, which is the defect.
    """

    offenders = [
        str(path.relative_to(SOURCE_ROOT))
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if re.search(r"['\"]unknown_metric['\"]", path.read_text())
    ]
    assert not offenders, "the retired spelling survives in:\n" + "\n".join(offenders)


def test_the_adapter_emits_exactly_its_declared_reason_set() -> None:
    """An unregistered adapter reason fails here instead of shipping."""

    assert _producer_reason_literals("integrations/health_flow.py") == set(
        PRODUCER_ELIGIBLE_REASONS["integrations/health_flow.py"]
    )


def test_the_matcher_emits_exactly_its_declared_reason_set() -> None:
    assert _producer_reason_literals("core/matching.py") == set(
        PRODUCER_ELIGIBLE_REASONS["core/matching.py"]
    )


def test_the_producer_scan_actually_catches_an_unregistered_reason() -> None:
    """A guard that cannot fail is not a guard.

    The scan reads literal emissions only: a call passing a constant is not
    something the extractor can resolve, so the declaration is what an editor must
    keep honest.
    """

    def emitted(text: str) -> set[str]:
        found = set(re.findall(r"_skip(?:_row)?\([^)]*?['\"]([a-z_]+)['\"]", text))
        found |= set(re.findall(r"['\"]reason['\"]\s*:\s*['\"]([a-z_]+)['\"]", text))
        return found

    declared = set(PRODUCER_ELIGIBLE_REASONS["integrations/health_flow.py"])
    assert emitted('skipped.append(_skip(position, "unknown_metric_code"))') != declared
    assert emitted('skipped.append(_skip(position, "brand_new_reason"))') != declared
    # A row literal counts too.
    assert emitted('rows.append({"reason": "unregistered"})') != declared
    # A constant is invisible to the scan — the extractor cannot resolve it, which
    # is exactly why the declaration exists beside it.
    assert emitted("skipped.append(_skip(position, UNKNOWN_METRIC))") == set()
    # And a fully registered source compares equal.
    registered = "\n".join(
        f'rows.append({{"reason": "{reason}"}})' for reason in sorted(declared)
    )
    assert emitted(registered) == declared


def test_every_producer_reason_is_a_string_and_the_sets_are_disjoint_from_the_bookkeeping() -> None:
    for producer, reasons in PRODUCER_ELIGIBLE_REASONS.items():
        assert producer, "a producer with no source path cannot be guarded"
        assert reasons and all(isinstance(r, str) and r for r in reasons), producer


def test_the_classification_fails_closed_on_an_unknown_reason() -> None:
    assert disposition_class("within_reference_range") == NORMAL
    assert disposition_class(UNKNOWN_METRIC) == UNUSABLE
    assert disposition_class("invalid_value") == UNUSABLE
    # The whole point: an unrecognised reason is never called normal.
    for reason in ("something_new", "", "normal"):
        assert disposition_class(reason) == UNUSABLE, reason


def test_only_the_unknown_metric_reason_is_unreadable() -> None:
    assert is_unreadable(UNKNOWN_METRIC)
    for reason in SKIP_REASONS:
        if reason != UNKNOWN_METRIC:
            assert not is_unreadable(reason), reason


def test_the_count_reads_the_one_spelling_the_producer_emits() -> None:
    assert unreadable_count([{"reason": UNKNOWN_METRIC}]) == 1
    assert unreadable_count([{"reason": "unknown_metric"}]) == 0
    assert unreadable_count([{"reason": "within_reference_range"}]) == 0
    assert unreadable_count([{"record_index": "1", "reason": UNKNOWN_METRIC}]) == 1


def test_the_adapter_rows_reach_the_rider_end_to_end() -> None:
    """The case that was unreachable: the adapter's own row, read by the reader.

    Before this module the adapter emitted `unknown_metric` and the reader counted
    `unknown_metric_code`, so this exact chain produced no rider at all.
    """

    from genesis_evidence.integrations.health_flow import build_evidence_request

    result = build_evidence_request(
        [{"metric_name": "C-Reactive Protein", "metric_value": "12", "unit": "mg/L",
          "reference_range": "0-5", "evidence_text": "C-Reactive Protein 12 mg/L 0-5 H",
          "page_number": 1}],
        confirmed=True,
    )
    assert result.uncovered, "the adapter produced no unreadable row to carry"
    reply = patient_reply_v3([], [], result.skipped)
    assert "不在当前解读范围内" in reply["summary"]
    assert "1 项" in reply["summary"]


def test_a_normal_row_is_never_counted_as_unusable() -> None:
    """`{within_reference_range, unknown_metric_code}` is not "2 items unusable"."""

    rows = [{"reason": "within_reference_range"}, {"reason": UNKNOWN_METRIC}]
    summary = empty_summary(rows)
    assert summary != ALL_IN_RANGE_SUMMARY
    # It reports what was not used, and the rider reports the unreadable ones — the
    # normal row is not in either count.
    combined = with_rider(summary, rows)
    assert "2 项" in combined
    assert combined.count("不在当前解读范围内") == 1


def test_the_empty_summaries_are_decided_by_class_not_by_set_equality() -> None:
    assert empty_summary([]) == NO_CARD_SUMMARY
    assert empty_summary([{"reason": "within_reference_range"}]) == ALL_IN_RANGE_SUMMARY
    assert empty_summary([{"reason": "confirmation_required"}]) == DEFERRED_SUMMARY
    assert empty_summary([{"reason": UNKNOWN_METRIC}]) == ""
    assert "2 项" in empty_summary([{"reason": "invalid_value"}] * 2)
    # A never-seen reason counts as unusable, and can never be called all-in-range.
    assert empty_summary([{"reason": "brand_new"}]) != ALL_IN_RANGE_SUMMARY


def test_the_rider_is_empty_when_nothing_is_unreadable() -> None:
    assert unreadable_rider(0) == ""
    assert with_rider("x", []) == "x"
    assert with_rider("x", None) == "x"
    assert with_rider("x", [{"reason": "invalid_value"}]) == "x"
    assert unreadable_rider(3) == with_rider("", [{"reason": UNKNOWN_METRIC}] * 3)


def test_the_two_v2_paths_word_the_same_result_identically() -> None:
    """The report path and the legacy projection must not diverge on patient copy."""

    from genesis_evidence.core.store.evidence import _v2_patient_reply

    finding = {
        "condition_code": "C",
        "condition_name": "N",
        "urgency": "routine",
        "abnormality_severity": 1,
        "evidence_strength": "low",
        "needs_recheck": True,
        "department": "D",
        "recheck_direction": "R",
        "card": {"id": "card-1", "version": "1", "evidence_profile_id": "p",
                 "patient_visible_body": "b", "sources": []},
        "source_observation_ids": ["o1"],
        "source_observations": [],
        "content_layer": "context_only",
        "action_status": "not_available",
        "action_message": "",
    }
    skipped = [{"reason": UNKNOWN_METRIC}] * 3
    projected = patient_reply_v2([finding], [], skipped)
    legacy = _v2_patient_reply([finding], [], skipped)
    assert projected["summary"] == legacy["summary"]
    assert "3 项" in projected["summary"]


def test_the_v2_no_findings_sentence_is_unchanged_by_this_change() -> None:
    """v2 is a live external contract; #261 must not rewrite its copy.

    v2's no-findings sentence is generic — honest both when no rows were passed and
    when every row was in range. Substituting v3's all-in-range sentence would be a
    contract change arriving as a side effect, which the issue's acceptance forbids.
    Measured against `main` on the live API: identical for both the no-rows and the
    all-in-range case.
    """

    assert patient_reply_v2([], [])["summary"] == NO_CARD_SUMMARY
    assert (
        patient_reply_v2([], [], [{"reason": "within_reference_range"}])["summary"]
        == NO_CARD_SUMMARY
    )
    # v3, which owns the empty-case classification, does distinguish them.
    assert (
        patient_reply_v3([], [], [{"reason": "within_reference_range"}])["summary"]
        == ALL_IN_RANGE_SUMMARY
    )
    # The rider is still the one thing v2 gained.
    assert "不在当前解读范围内" in patient_reply_v2(
        [], [], [{"reason": UNKNOWN_METRIC}]
    )["summary"]


def test_patient_reply_v2_carries_the_rider_it_structurally_could_not_before() -> None:
    """The report path is the one caller of this builder; it must be able to warn."""

    assert "不在当前解读范围内" not in patient_reply_v2([], [])["summary"]
    reply = patient_reply_v2([], [], [{"reason": UNKNOWN_METRIC}])
    assert "不在当前解读范围内" in reply["summary"]


def test_the_no_published_card_value_is_owned_here_and_used_by_the_contract() -> None:
    from genesis_evidence.core.contracts import EvidenceUnmatched, EvidenceUnmatchedV2

    assert NO_PUBLISHED_CARD == "no_published_knowledge_card"
    assert get_args(EvidenceUnmatched.model_fields["reason"].annotation) == (NO_PUBLISHED_CARD,)
    assert get_args(EvidenceUnmatchedV2.model_fields["reason"].annotation) == (NO_PUBLISHED_CARD,)


def test_urgency_rank_fails_closed_by_name_rather_than_raising_a_key_error() -> None:
    with pytest.raises(UnknownDispositionValue, match="urgency level"):
        urgency_rank("whenever")
    for value in ("", "routine ", None):
        with pytest.raises(UnknownDispositionValue):
            urgency_rank(value)  # type: ignore[arg-type]


def test_the_rank_is_a_total_order_over_the_vocabulary() -> None:
    assert set(URGENCY_RANK) == set(URGENCY_LEVELS)
    assert sorted(URGENCY_RANK.values()) == list(range(len(URGENCY_LEVELS)))
    assert urgency_rank("emergency") < urgency_rank("urgent") < urgency_rank("soon")


def test_no_module_sorts_on_a_hand_written_urgency_map() -> None:
    offenders = [
        str(path.relative_to(SOURCE_ROOT))
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if re.search(r"\[\s*['\"]urgency['\"]\s*\]", path.read_text())
        and re.search(r"\{[^{}]*emergency['\"]\s*:\s*0", path.read_text())
    ]
    assert not offenders, "urgency map used inline:\n" + "\n".join(offenders)
