"""One owner of what a knowledge card's lifecycle says (#221).

A card's lifecycle is three pieces of knowledge: which statuses exist, which
moves between them are legal, and which of them a patient may see. Each used to
be declared in several places across `core.contracts`, `core.store`, and
`review.service`, in Python and in JavaScript, and the copies had already
started to disagree — the coverage tally had silently dropped `rejected`, and
the rule that removes a `very_low` card from the patient pool was enforced by
the writer and stated by none of the readers.

This module is pure constants and pure functions over strings. It imports
nothing from `core.store`, `review`, or `literature`, so every layer can depend
on it without an import cycle — the discipline `core.methodology` (#218),
`core.consistency` (#214), and `core.source_excerpt` (#202) already follow. That
is also what makes the rules testable without a SQLite fixture: everything here
is a function of a status string.

The SQL `CHECK` in `core.store.schema` stays a literal — a schema string cannot
import Python — and `tests/test_card_lifecycle.py` extracts it from the source
and compares the value sets, as `tests/test_methodology.py` does for
`corrected_study_design`.
"""

from __future__ import annotations

from typing import Literal

# The two grade thresholds are named slices of the evidence-strength vocabulary,
# not of the lifecycle; `core.evidence_strength` (#238) owns the vocabulary they
# slice, so a change to "which strengths a patient may see" is one edit there.
from .evidence_strength import (
    ACTION_THRESHOLD_STRENGTHS,
    PATIENT_VISIBLE_STRENGTHS,
)

#: The one status vocabulary. Every other declaration derives from it.
CARD_STATUSES: tuple[str, ...] = (
    "draft",
    "in_review",
    "approved",
    "published",
    "rejected",
    "stale",
)

#: A card that has left the patient pool for good: retired evidence, or a
#: refused claim. Both are terminal for this card; a return to the pool is a new
#: card at a new version, not a transition.
TERMINAL_STATUSES: frozenset[str] = frozenset({"rejected", "stale"})

#: Statuses a card can still move out of. The complement of TERMINAL_STATUSES
#: within the vocabulary, declared as a value so a reader does not have to
#: subtract.
NON_TERMINAL_STATUSES: frozenset[str] = frozenset(CARD_STATUSES) - TERMINAL_STATUSES

#: The retirement status itself. Named because `retirement.py` and the coverage
#: projection both single it out.
RETIRED_STATUS = "stale"

#: Grades whose strength is enough to carry a patient-visible context card.
#: Derived from `core.evidence_strength`, which owns the vocabulary; the name is
#: kept here because `is_patient_visible` is the lifecycle predicate that reads it.
PATIENT_VISIBLE_GRADES: frozenset[str] = PATIENT_VISIBLE_STRENGTHS

#: The grades that additionally clear the action-advice threshold. A different
#: question from PATIENT_VISIBLE_GRADES — it decides the message a patient reads,
#: not whether the card appears — so it is a separate declaration rather than a
#: reuse.
ACTION_THRESHOLD_GRADES: frozenset[str] = ACTION_THRESHOLD_STRENGTHS

#: The legal transition graph, as data. A status absent as a key has no legal
#: outgoing move: `published`, `rejected` and `stale` are sinks here, and a
#: published card leaves the pool by being superseded, not by transitioning.
CARD_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"in_review", "rejected"}),
    "in_review": frozenset({"approved", "rejected"}),
    "approved": frozenset({"published", "rejected"}),
}

#: The path the autonomous reviewer walks, read from the graph rather than
#: restated as a chain of `if`s.
AUTONOMOUS_REVIEW_PATH: tuple[str, ...] = ("draft", "in_review", "approved", "published")

#: The contract type, derived so `contracts.CardStatus` and the store cannot
#: diverge. `Literal[*...]` needs a tuple, not a list.
CardStatus = Literal[*CARD_STATUSES]


def patient_visible_sql(column: str) -> str:
    """The `is_patient_visible` rule as a SQL predicate for `column`.

    Four card-selection queries state the rule in SQL because they must filter in
    the database, not after fetching. Rendering the predicate from the module
    keeps those four from being a fifth declaration: `column` is a caller-supplied
    column name, never a value, and every value interpolated is a literal from
    this module.
    """

    grades = ", ".join(f"'{grade}'" for grade in sorted(PATIENT_VISIBLE_GRADES))
    return f"{column}.status = 'published' AND {column}.grade IN ({grades})"


def next_statuses(status: str) -> tuple[str, ...]:
    """The legal targets from `status`, in graph order, empty when it is a sink.

    Empty is the honest answer for both "terminal" and "unknown": neither offers
    a move, and a caller must not read it as "anything is allowed".
    """

    return tuple(sorted(CARD_TRANSITIONS.get(status, frozenset())))


def can_transition(status: str, target: str) -> bool:
    """Whether `status -> target` is one of the legal moves."""

    return target in CARD_TRANSITIONS.get(status, frozenset())


def is_terminal(status: str) -> bool:
    """Whether the card has left the active lifecycle for good."""

    return status in TERMINAL_STATUSES


def is_patient_visible(status: str, grade: str | None) -> bool:
    """Whether a card in this status and grade may reach a patient.

    The one predicate. It is what `transition_card` refuses on and what every
    published-card reader selects on, so the writer and the readers cannot
    disagree — which is how a `very_low` card could previously be published-but-
    invisible for a reason no reader stated.
    """

    return status == "published" and grade in PATIENT_VISIBLE_GRADES


def demo() -> None:
    """Smallest runnable check for the rules that must not silently move."""

    assert set(CARD_STATUSES) == set(NON_TERMINAL_STATUSES | TERMINAL_STATUSES)
    assert not (NON_TERMINAL_STATUSES & TERMINAL_STATUSES)
    # Every target in the graph is a real status.
    for source, targets in CARD_TRANSITIONS.items():
        assert source in CARD_STATUSES, source
        for target in targets:
            assert target in CARD_STATUSES, target
            assert target != source, f"{source} -> itself is not a move"
    # The autonomous path is a walk of the graph, not a parallel description.
    for source, target in zip(
        AUTONOMOUS_REVIEW_PATH, AUTONOMOUS_REVIEW_PATH[1:], strict=False
    ):
        assert can_transition(source, target), f"{source} -> {target}"
    # Sinks offer nothing, and unknown statuses are not wide open.
    for sink in ("published", "rejected", "stale"):
        assert next_statuses(sink) == (), sink
    assert next_statuses("not_a_status") == ()
    # `published` is a graph sink but not terminal: it leaves the pool by being
    # superseded, which is a write to the row, not a transition out of it.
    assert not is_terminal("published")
    assert is_terminal("rejected") and is_terminal("stale")
    assert "published" in NON_TERMINAL_STATUSES
    # Visibility is status AND grade.
    assert is_patient_visible("published", "low")
    assert not is_patient_visible("published", "very_low")
    assert not is_patient_visible("published", None)
    assert not is_patient_visible("approved", "high")
    assert not is_patient_visible("stale", "high")
    print(f"card_statuses={len(CARD_STATUSES)} transitions={len(CARD_TRANSITIONS)} ok")


if __name__ == "__main__":
    demo()
