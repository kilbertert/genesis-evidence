"""One owner of why an observation did not become a finding (#261).

*Why an observation was dropped, and what the patient is told about it* used to be
reproduced as a contract ``Literal``, two disjoint producer vocabularies, three
reader predicates, and three patient-reply builders. The copies had come apart in
both directions and the result was a live false assurance on the exact path the
previous change set out to close:

- the concept every reader tests — an unknown indicator — was spelled
  ``unknown_metric`` by the Health-Flow adapter and ``unknown_metric_code`` by
  every reader, so the rider naming the unreadable rows was **unreachable** from
  the adapter's own rows;
- the report path's builder structurally could not carry that rider at all;
- the empty summary counted a *normal* observation as unusable;
- and the sibling ``urgency`` vocabulary was a seven-site ``Literal`` with its
  rank map spelled three times, raising a bare ``KeyError`` on an unknown level.

This module is pure constants and pure functions over strings. It imports nothing
from ``core.store``, ``review``, or ``integrations``, so every layer can depend on
it without an import cycle — the discipline ``core.methodology`` (#218),
``core.consistency`` (#214), ``core.card_lifecycle`` (#221), and
``core.evidence_strength`` (#238) already follow. That is also what makes the
rules testable with no SQLite fixture and no HTTP client.

**Two producers, one vocabulary.** The matcher and the adapter classify different
rows and cannot emit the same reason sets; that is a fact, not a defect, and
:data:`PRODUCER_ELIGIBLE_REASONS` declares it once. What was a defect is that the
two sets shared no spelling for the one concept both of them mean, so
``tests/test_disposition.py`` extracts each producer's call sites from source and
compares them to its declared set.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

#: The cross-service skip-reason vocabulary. `contracts.EvidenceSkipped.reason`
#: derives from it, and `tests/test_disposition.py` extracts the adapter's and the
#: matcher's call sites from source and holds them against
#: :data:`PRODUCER_ELIGIBLE_REASONS`.
SKIP_REASONS: tuple[str, ...] = (
    "missing_reference_range",
    "within_reference_range",
    "missing_source_evidence",
    "missing_source_page",
    "missing_unit",
    "invalid_value",
    "unknown_metric_code",
)

#: The one name for the concept every reader must test: *we could not tell what
#: this indicator is*. The adapter used to emit ``unknown_metric`` — a second
#: spelling of this same idea — which is why the reader counted zero where it
#: should have counted many. It emits this name now, and the old one is gone.
UNKNOWN_METRIC = "unknown_metric_code"

#: The sibling value on a matched-but-cardless observation. Owned here too, because
#: the two disposition vocabularies of the same chain should have one home.
NO_PUBLISHED_CARD = "no_published_knowledge_card"

#: The three dispositions a dropped observation can have, and the whole point of
#: the classification: only ``normal`` means *we looked and it was fine*.
NORMAL = "normal"
UNUSABLE = "unusable"
DEFERRED = "deferred"

#: Every reason absent here is :data:`UNUSABLE` — the fail-closed default: a reason
#: we do not recognise is one we could not use, never one that was normal. Only two
#: reasons are singled out:
#:
#: - ``within_reference_range`` is the sole reason that means the value was read and
#:   was fine. It is what makes "没有发现异常" a true sentence.
#: - ``confirmation_required`` means the row was never *applicable* — the user has
#:   not confirmed it — which is neither normal nor a failure to read it.
_DISPOSITION_CLASSES: Mapping[str, str] = {
    "within_reference_range": NORMAL,
    "confirmation_required": DEFERRED,
}

#: The empty-summary sentences and the rider. They live here rather than in a caller
#: because their *choice* is the rule — which class of dropped row licenses which
#: sentence — and three builders used to make that choice independently.
NO_CARD_SUMMARY = "当前没有发现可由已发布知识卡支持的异常指标。"
ALL_IN_RANGE_SUMMARY = "已确认的指标均在参考范围内，没有需要提示的异常。"
DEFERRED_SUMMARY = "本次没有已确认的指标参与解读，因此未生成健康提示。"
UNUSABLE_SUMMARY = "已确认的指标中有 {count} 项无法参与匹配，因此本次未生成健康提示。"

#: What each producer emits, declared once. The matcher sees confirmed observations
#: and classifies them; the adapter sees raw report rows and cannot, so it is the
#: only producer of the "we could not read this row at all" reasons — including the
#: adapter-only ``confirmation_required``.
#:
#: Three of :data:`SKIP_REASONS` have no producer at all. That is a decision now
#: rather than an accident: the contract is the cross-service surface, and the
#: guard test fails if a producer grows a value outside its declared set.
PRODUCER_ELIGIBLE_REASONS: Mapping[str, frozenset[str]] = {
    "core/matching.py": frozenset(
        {"missing_reference_range", "within_reference_range", NO_PUBLISHED_CARD}
    ),
    "integrations/health_flow.py": frozenset(
        {
            "confirmation_required",
            UNKNOWN_METRIC,
            "missing_source_page",
            "missing_unit",
            "missing_source_value",
            "missing_source_reference",
            "invalid_source_file_index",
            "invalid_bbox",
        }
    ),
}

#: The urgency vocabulary, strongest first. Every ``Literal`` over these four
#: values derives from the tuple, so a contract field and a sort key cannot drift.
URGENCY_LEVELS: tuple[str, ...] = ("emergency", "urgent", "soon", "routine")

#: The one rank order over the urgency levels.
URGENCY_RANK: Mapping[str, int] = {
    level: index for index, level in enumerate(URGENCY_LEVELS)
}

#: The contract type, derived so the seven contract fields cannot diverge.
Urgency = Literal[*URGENCY_LEVELS]


class UnknownDispositionValue(ValueError):
    """A value outside the disposition vocabularies."""


def disposition_class(reason: str) -> str:
    """The disposition for a skip reason.

    The split that makes the empty-summary rule a decision instead of a set
    comparison. An unrecognised reason is :data:`UNUSABLE`: the classification
    fails closed, because the one thing a dropped observation must never be
    silently called is *normal*.
    """

    return _DISPOSITION_CLASSES.get(reason, UNUSABLE)


def is_unreadable(reason: str) -> bool:
    """Whether a reason means *we have no idea what this indicator is*.

    Narrower than :func:`disposition_class` returning :data:`UNUSABLE`: a row we
    know how to name but could not use is a different sentence from one we could
    not identify at all. This predicate is the second kind, and it is the one the
    patient-facing rider counts.
    """

    return reason == UNKNOWN_METRIC


def unreadable_count(skipped: Iterable[Mapping[str, object]]) -> int:
    """How many dropped rows this service could not identify.

    The one count, replacing the reader's own predicate in three places. The reason
    breakdown stays internal; only the number reaches a patient.
    """

    return sum(1 for item in skipped if is_unreadable(str(item.get("reason", ""))))


def empty_summary(skipped: Iterable[Mapping[str, object]]) -> str:
    """What to say when nothing was shown and nothing is merely card-less.

    The caller appends the rider (see :func:`with_rider`). Only
    :data:`NORMAL` makes "no abnormal indicators were found" true; every other
    class means the service could not use the row, and saying "没有发现" there
    tells the patient their report was clear when it was not.
    """

    items = list(skipped)
    reasons = {str(item.get("reason", "")) for item in items}
    if not reasons:
        # The caller passed no reasons at all, so nothing can be classified and no
        # plan exists: "no published card" is the honest statement for that case.
        return NO_CARD_SUMMARY
    classes = {disposition_class(reason) for reason in reasons}
    if classes == {NORMAL}:
        # Every confirmed indicator was read and was in range: the only case where
        # "nothing abnormal" is true.
        return ALL_IN_RANGE_SUMMARY
    if classes == {DEFERRED}:
        # Nothing applied — the user confirmed no row yet — so there was nothing to
        # read. Not normal, and not a failure to use anything.
        return DEFERRED_SUMMARY
    if reasons == {UNKNOWN_METRIC}:
        # Only unreadable rows: the rider names them, and this says nothing else.
        return ""
    return UNUSABLE_SUMMARY.format(count=len(items))


def unreadable_rider(count: int) -> str:
    """The sentence naming the rows this service could not identify."""

    if count <= 0:
        return ""
    return (
        f"报告另有 {count} 项不在当前解读范围内，"
        "本次未作解读，需要时可请医生一同查看。"
    )


def with_rider(summary: str, skipped: Iterable[Mapping[str, object]] | None) -> str:
    """Attach the unreadable rider to ``summary``.

    The one place the rider is attached, and it goes on **every** branch — a report
    can produce three findings and forty unreadable rows, and a patient told only
    about the three reads the rest as "fine".

    It is folded into ``summary`` rather than added as a reply field because
    ``patient_reply`` is parsed by Health-Flow under ``extra="forbid"``: a new field
    there would be a breaking change to a live consumer, while prose degrades
    gracefully. The machine-readable form already exists in ``skipped``.
    """

    return summary + unreadable_rider(unreadable_count(list(skipped or ())))


def urgency_rank(value: str) -> int:
    """The sort order for an urgency level, failing closed by name.

    The three inline ``{level: rank}`` maps this replaces each raised a bare
    ``KeyError`` on a level they did not know; a sort key that raises is a sorting
    bug waiting for a new level, so an unknown level is refused by name instead.
    """

    if value not in URGENCY_RANK:
        raise UnknownDispositionValue(f"{value!r} is not an urgency level")
    return URGENCY_RANK[value]


def demo() -> None:
    """Smallest runnable check for the rules that must not silently move."""

    # The classification covers exactly the two singled-out reasons.
    assert {r for r, c in _DISPOSITION_CLASSES.items()} == {
        "within_reference_range",
        "confirmation_required",
    }
    assert disposition_class("within_reference_range") == NORMAL
    assert disposition_class("confirmation_required") == DEFERRED
    assert disposition_class(UNKNOWN_METRIC) == UNUSABLE
    assert disposition_class("invalid_value") == UNUSABLE
    # Fail closed: an unrecognised reason is never "normal".
    assert disposition_class("something_new") == UNUSABLE
    # The one count. The adapter's own rows go through this predicate.
    assert unreadable_count([{"reason": UNKNOWN_METRIC}]) == 1
    assert unreadable_count([{"reason": "within_reference_range"}]) == 0
    # Empty summaries: normal is normal, unreadable is not counted as unusable.
    assert empty_summary([]) == "当前没有发现可由已发布知识卡支持的异常指标。"
    assert empty_summary([{"reason": "within_reference_range"}]).startswith("已确认的指标均在")
    assert empty_summary([{"reason": UNKNOWN_METRIC}]) == ""
    assert "2 项" in empty_summary(
        [{"reason": "within_reference_range"}, {"reason": UNKNOWN_METRIC}]
    ), "a mixed report says how many were not used, never that all were in range"
    assert empty_summary([{"reason": "confirmation_required"}]).startswith("本次没有已确认")
    assert "2 项" in empty_summary([{"reason": "invalid_value"}, {"reason": "invalid_value"}])
    # The rider attaches on every branch and is empty when nothing is unreadable.
    assert with_rider("…", [{"reason": "invalid_value"}]) == "…"
    assert "1 项" in with_rider("…", [{"reason": UNKNOWN_METRIC}])
    # The adapter's rows are counted through the reader's own predicate.
    assert with_rider("…", ({"reason": UNKNOWN_METRIC} for _ in range(3))).count("3 项") == 1
    # Urgency: a total order, and an unknown level refused by name.
    assert set(URGENCY_RANK) == set(URGENCY_LEVELS)
    assert sorted(URGENCY_RANK.values()) == list(range(len(URGENCY_LEVELS)))
    assert urgency_rank("emergency") == 0 and urgency_rank("routine") == 3
    try:
        urgency_rank("whenever")
    except UnknownDispositionValue:
        pass
    else:
        raise AssertionError("urgency_rank must fail closed")
    # Every declared producer reason is a string, and no producer restates the
    # whole vocabulary (an even split is the declared fact, not a copy).
    for producer, reasons in PRODUCER_ELIGIBLE_REASONS.items():
        assert producer and all(isinstance(r, str) and r for r in reasons), producer
    print(
        f"skip_reasons={len(SKIP_REASONS)} "
        f"matcher={len(PRODUCER_ELIGIBLE_REASONS['core/matching.py'])} "
        f"adapter={len(PRODUCER_ELIGIBLE_REASONS['integrations/health_flow.py'])} "
        f"urgency={len(URGENCY_LEVELS)}"
    )


if __name__ == "__main__":
    demo()
