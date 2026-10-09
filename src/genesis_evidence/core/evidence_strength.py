"""One owner of the evidence-strength vocabulary (#238).

How strong a piece of evidence is, what it is called, how it orders, and which of
its values clear each threshold used to be declared in ten places across
`core.contracts`, `core.store`, `core.matching`, `core.card_lifecycle`,
`review.service`, and the review workbench's browser code. The copies had already
come apart: the workbench told the reviewer that `low` certainty is withheld from
patients, while the publish gate ships it as a visible context card, and nothing
compared the two.

This module is pure constants and pure functions over strings. It imports nothing
from `core.store`, `review`, or `literature`, so every layer can depend on it
without an import cycle — the discipline `core.methodology` (#218),
`core.consistency` (#214), `core.source_excerpt` (#202), and
`core.card_lifecycle` (#221) already follow. That is also what makes the rules
testable without a SQLite fixture or a browser: everything here is a function of
a strength string.

**One value class, two names.** A profile's `certainty` and a card's `grade` are
the same value class — `review.service` writes the profile field straight into
the card field — so both are typed from :data:`EVIDENCE_STRENGTHS` and cannot
drift. The field names stay as they are; they are a persistence spelling, not a
second vocabulary.

**`mixed` is an aggregate, not a grade.** A finding may combine several component
cards whose grades disagree. `evidence_strength_summary` reports that as
:data:`MIXED` rather than quoting one card, so it is deliberately *not* a member
of the base vocabulary: no card can have it and :func:`rank` refuses to order it,
by name, instead of raising a bare ``KeyError``.

**What cannot be single-sourced.** A schema string cannot import Python, so the
``evidence_profiles.certainty`` and ``knowledge_cards.grade`` ``CHECK``
constraints stay literal. They are not unguarded: an edit that lets one drift from
:data:`EVIDENCE_STRENGTHS` fails ``tests/test_evidence_strength.py``, which
extracts it from the source and compares the value sets. The workbench cannot
import Python either, so it *fetches* the vocabulary and the guidance sentence
from the review API instead — the pattern `#221` established for the transition
graph.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

#: The one base vocabulary: a card's grade, and a profile's certainty.
EVIDENCE_STRENGTHS: tuple[str, ...] = ("high", "moderate", "low", "very_low")

#: The finding-level aggregate for cards that disagree. Named because
#: `matching.evidence_strength_summary` and the action message both single it out.
MIXED = "mixed"

#: The base vocabulary as a contract type. Every ``Literal`` over these four values
#: derives from the tuple above, so a contract field and the store cannot diverge.
CardGrade = Literal[*EVIDENCE_STRENGTHS]

#: The finding-level type: a card grade, or the aggregate of several that disagree.
EvidenceStrength = Literal[*EVIDENCE_STRENGTHS, MIXED]

#: The rank order over the base vocabulary — index 0 is the strongest.
EVIDENCE_RANK: Mapping[str, int] = {"high": 0, "moderate": 1, "low": 2, "very_low": 3}

#: Grades whose strength is enough to carry a patient-visible context card.
#: `very_low` is deliberately absent: such a card is draftable and reviewable, but
#: may not be published.
PATIENT_VISIBLE_STRENGTHS: frozenset[str] = frozenset({"high", "moderate", "low"})

#: The grades that additionally clear the action-advice threshold. A different
#: question from :data:`PATIENT_VISIBLE_STRENGTHS` — it decides the message a
#: patient reads, not whether the card appears — so it is a separate declaration
#: rather than a reuse.
ACTION_THRESHOLD_STRENGTHS: frozenset[str] = frozenset({"high", "moderate"})

#: The slice between them: visible to a patient as context, but not enough to
#: carry an action message. Derived rather than restated, because "the difference
#: of the two thresholds" is exactly what the coverage ladder and the workbench
#: sentence mean — and widening either threshold must move this one with it.
CONTEXT_ONLY_STRENGTHS: frozenset[str] = PATIENT_VISIBLE_STRENGTHS - ACTION_THRESHOLD_STRENGTHS

#: Display names. The one place the labels are spelled, so the workbench and any
#: later patient-copy surface cannot disagree about what a strength is called.
STRENGTH_LABELS: Mapping[str, str] = {
    "high": "高",
    "moderate": "中等",
    "low": "低",
    "very_low": "极低",
}

#: The GRADE arithmetic's ordinal → value map, derived from the one rank order so
#: the two cannot disagree about which grade is stronger. The arithmetic caps at
#: `moderate`; the derivation covers `high` too, so a future cap change needs no
#: second edit here.
STRENGTH_BY_SCORE: Mapping[int, str] = {
    len(EVIDENCE_STRENGTHS) - 1 - index: strength
    for strength, index in EVIDENCE_RANK.items()
}


class UnknownEvidenceStrength(ValueError):
    """A value outside the strength vocabulary, or one that cannot be ordered."""


def rank(strength: str) -> int:
    """The sort order for a card grade.

    Fails closed by name on :data:`MIXED` and on anything else: `mixed` is an
    aggregate over cards, not a grade one of them holds, so it has no place in a
    sort key and a caller must decide what to do rather than receive a `KeyError`
    from a dict that silently has only four keys.
    """

    if strength not in EVIDENCE_RANK:
        detail = "is an aggregate, not a card grade" if strength == MIXED else "is not a grade"
        raise UnknownEvidenceStrength(f"{strength!r} {detail}")
    return EVIDENCE_RANK[strength]


def strength_label(strength: str) -> str:
    """The display name for a strength, failing closed by name on an unknown one."""

    if strength not in STRENGTH_LABELS:
        raise UnknownEvidenceStrength(f"{strength!r} has no display name")
    return STRENGTH_LABELS[strength]


def strength_for_score(score: int) -> str:
    """The grade the GRADE arithmetic's ordinal denotes."""

    if score not in STRENGTH_BY_SCORE:
        raise UnknownEvidenceStrength(f"GRADE score {score!r} is outside 0..3")
    return STRENGTH_BY_SCORE[score]


def evidence_strength_summary(grades: Iterable[str]) -> str:
    """The shared grade of a finding's component cards, or :data:`MIXED`.

    The aggregation rule that creates the fifth value. One metric can carry
    several component cards (ADR 0007) and they need not share a grade, so
    collapsing them to one level would quote one card's verdict as if it were all
    of them.
    """

    unique = set(grades)
    return next(iter(unique)) if len(unique) == 1 else MIXED


def visibility_guidance() -> str:
    """The sentence the reviewer workbench shows beside the strength selector.

    Derived from the two threshold sets, so the sentence that explains the gate
    cannot contradict the gate itself — the previous hand-written copy told the
    reviewer that `low` certainty is withheld, while
    :data:`PATIENT_VISIBLE_STRENGTHS` publishes it as a visible context card.
    """

    withheld = "、".join(
        strength_label(strength)
        for strength in sorted(set(EVIDENCE_STRENGTHS) - PATIENT_VISIBLE_STRENGTHS, key=rank)
    )
    context_only = "、".join(
        strength_label(strength) for strength in sorted(CONTEXT_ONLY_STRENGTHS, key=rank)
    )
    clauses: list[str] = []
    if withheld:
        clauses.append(f"{withheld}确定性内容不会发布到患者端")
    if context_only:
        clauses.append(f"{context_only}确定性内容仅作为证据背景卡发布，行动建议需达到中等或高确定性")
    return "；".join(clauses) + "。"


def strength_vocabulary() -> dict[str, object]:
    """The vocabulary as the workbench consumes it.

    The browser cannot import Python, so the review API serves this and the page
    renders its `<select>` and its guidance sentence from what it is given rather
    than from a hand-copied list.
    """

    return {
        "values": list(EVIDENCE_STRENGTHS),
        "labels": {strength: STRENGTH_LABELS[strength] for strength in EVIDENCE_STRENGTHS},
        "patient_visible": sorted(PATIENT_VISIBLE_STRENGTHS, key=rank),
        "action_threshold": sorted(ACTION_THRESHOLD_STRENGTHS, key=rank),
        "visibility_guidance": visibility_guidance(),
    }


def demo() -> None:
    """Smallest runnable check for the rules that must not silently move."""

    # The rank is a total order over exactly the base vocabulary.
    assert set(EVIDENCE_RANK) == set(EVIDENCE_STRENGTHS)
    assert sorted(EVIDENCE_RANK.values()) == list(range(len(EVIDENCE_STRENGTHS)))
    # The ordinal map inverts the rank without restating it.
    for strength, index in EVIDENCE_RANK.items():
        assert strength_for_score(len(EVIDENCE_STRENGTHS) - 1 - index) == strength
    # Thresholds are nested, and visibility is the narrower question.
    assert ACTION_THRESHOLD_STRENGTHS < PATIENT_VISIBLE_STRENGTHS
    assert set(EVIDENCE_STRENGTHS) - PATIENT_VISIBLE_STRENGTHS == {"very_low"}
    # The aggregate is not a grade, and asking for its rank is refused by name.
    assert MIXED not in EVIDENCE_STRENGTHS
    try:
        rank(MIXED)
    except UnknownEvidenceStrength:
        pass
    else:
        raise AssertionError("rank(mixed) must fail closed")
    for other in ("", "unknown", None):
        try:
            rank(other)  # type: ignore[arg-type]
        except UnknownEvidenceStrength:
            pass
        else:
            raise AssertionError(f"rank({other!r}) must fail closed")
    # Aggregation: unanimous collapses, disagreement is mixed.
    assert evidence_strength_summary(["low", "low"]) == "low"
    assert evidence_strength_summary(["low", "moderate"]) == MIXED
    # The guidance names the withheld value and the context-only value, and does
    # not claim `low` is withheld.
    guidance = visibility_guidance()
    assert "极低确定性内容不会发布到患者端" in guidance
    assert "低确定性内容仅作为证据背景卡发布" in guidance
    assert "低或极低" not in guidance
    print(f"strengths={len(EVIDENCE_STRENGTHS)} guidance={guidance}")


if __name__ == "__main__":
    demo()
