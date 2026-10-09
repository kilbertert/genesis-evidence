"""One owner of a paper's publication integrity (#264).

*What a paper's publication integrity is, which statuses admit a paper, and which
statuses forbid durable evidence* had no owner. It was reproduced as a typed enum
in `literature`, a SQL `CHECK`, a validation `set`, a hand-written reader, three
write-side refusals, three acquisition-side checks, and seven SQL gates plus one
`CASE` branch in the review store — and the sixth free copy was the one that had
already come apart: the admission path wrote the rule as an equality in six places
and as an inequality in two, with no single statement of which statuses admit a
paper.

This module is pure constants and pure functions over strings. It imports nothing
from `core.store`, `review`, `literature`, or `integrations`, so every layer can
depend on it without an import cycle — the discipline `core.methodology` (#218),
`core.consistency` (#214), `core.card_lifecycle` (#221), `core.evidence_strength`
(#238), and `core.disposition` (#261) already follow.

**The refusal is a rule; the consequence is the caller's.** Naming
:func:`forbids_durable_evidence` makes the three store write paths one call, and
leaves each caller's error policy its own: `save_full_text` and
`enqueue_extraction` raise `PaperRetracted`, `save_ai_extraction` raises
`ValueError`. That inconsistency is deliberate here — it belongs to the caller —
and this module deliberately does not settle it. It is the same move
`core.methodology`'s docstring records.

**What cannot be single-sourced.** A schema string cannot import Python, so the
`papers.integrity_status` `CHECK` stays literal. It is not unguarded: an edit that
lets it drift from :data:`INTEGRITY_STATUSES` fails `tests/test_publication_integrity.py`,
which extracts it from the source and compares the value sets — the pattern
`tests/test_card_lifecycle.py` and `tests/test_methodology.py` already use.
"""

from __future__ import annotations

from typing import Literal

#: The one status vocabulary. The schema `CHECK`, the store's validation, and
#: `literature.integrity.IntegrityStatus` all derive from it.
INTEGRITY_STATUSES: tuple[str, ...] = (
    "clear",
    "updated",
    "corrected",
    "expression_of_concern",
    "retracted",
    "unknown",
)

#: A clean publication. The one status that admits a paper today.
CLEAR = "clear"

#: A withdrawn paper. The one status that forbids durable evidence today.
RETRACTED = "retracted"

#: The column's default and the reader's fallback: no provider has judged this yet.
UNKNOWN = "unknown"

#: Statuses that admit a paper into the evidence chain. Today it is `{clear}`, but
#: it is *declared* rather than inferred from an equality, so "which statuses admit
#: a paper" is a decision a maintainer can see and change in one place. `updated`
#: and `corrected` are deliberately absent: a correction does not license treating
#: the paper as clean without review.
ADMISSIBLE_STATUSES: frozenset[str] = frozenset({CLEAR})

#: Statuses that forbid an evidence write. A retracted paper must not gain durable
#: full text, an extraction job, or candidate claims — the rule `save_full_text`,
#: `enqueue_extraction` and `save_ai_extraction` each used to re-state by hand.
#:
#: Declared as a set rather than a single status because the question is "which
#: statuses forbid evidence", not "is this one retracted"; adding
#: `expression_of_concern` here is one edit, and every refusal follows.
FORBIDS_DURABLE_EVIDENCE: frozenset[str] = frozenset({RETRACTED})

#: The contract type, derived so a caller's annotation and the store cannot diverge.
IntegrityStatusValue = Literal[*INTEGRITY_STATUSES]


def admits_paper(status: str) -> bool:
    """Whether a paper in this integrity state may enter the evidence chain.

    The one predicate behind the admission gate, the claim-review approval gate,
    the card-claim eligibility check, and the coverage/candidate/publish SQL. It
    used to be an equality repeated in six places and an inequality in two, with no
    statement of the rule they shared.
    """

    return status in ADMISSIBLE_STATUSES


def forbids_durable_evidence(status: str) -> bool:
    """Whether this state forbids a durable evidence write.

    The write-side twin of :func:`admits_paper`, and a different question: a paper
    may be inadmissible without being forbidden (a `corrected` paper admits no
    claims, but nothing prevents storing the correction notice's own metadata).
    """

    return status in FORBIDS_DURABLE_EVIDENCE


def integrity_sql(column: str, *, admits: bool = True) -> str:
    """The `admits_paper` rule as a SQL predicate for `column`.

    Seven review-store queries and the queue projection state the rule in SQL
    because they must filter in the database, not after fetching. Rendering the
    predicate from the module keeps those from being an eighth declaration:
    `column` is a caller-supplied column name (or alias), never a value, and every
    value interpolated is a literal from this module.

    ``admits=False`` renders the negation, so the queue's `blocked` branch and the
    publish gate's `invalid` tally cannot come to mean something the admission gate
    does not.
    """

    values = ", ".join(f"'{status}'" for status in sorted(ADMISSIBLE_STATUSES))
    rendered = f"{column}.integrity_status IN ({values})"
    # The negation is rendered as `NOT IN (...)` and parenthesised, so a caller can
    # splice it into an OR chain: with a single admitted status it asks exactly the
    # question the original `<> 'clear'` did.
    return rendered if admits else f"NOT ({rendered})"


def is_retracted(status: str) -> bool:
    """Whether a stored status is exactly :data:`RETRACTED`.

    Narrower than :func:`forbids_durable_evidence` for the one caller that reports
    *what* happened (`papers.update_integrity`'s audit action), not *what follows*.
    """

    return status == RETRACTED


def demo() -> None:
    """Smallest runnable check for the rules that must not silently move."""

    # The vocabulary has no duplicates and names every member the code singles out.
    assert len(set(INTEGRITY_STATUSES)) == len(INTEGRITY_STATUSES)
    assert {CLEAR, RETRACTED, UNKNOWN} <= set(INTEGRITY_STATUSES)
    # Admission and forbiddenness are different questions over the same vocabulary,
    # and they do not overlap: nothing is both admitted and forbidden.
    assert set(INTEGRITY_STATUSES) >= ADMISSIBLE_STATUSES
    assert set(INTEGRITY_STATUSES) >= FORBIDS_DURABLE_EVIDENCE
    assert not (ADMISSIBLE_STATUSES & FORBIDS_DURABLE_EVIDENCE)
    # The predicate and the SQL rendering agree on every status.
    for status in INTEGRITY_STATUSES:
        assert admits_paper(status) is (status in ADMISSIBLE_STATUSES)
        assert forbids_durable_evidence(status) is (status in FORBIDS_DURABLE_EVIDENCE)
        if status != CLEAR:
            assert not admits_paper(status), status
    # An unknown status is not admitted and not forbidden: fail closed on
    # admission, open on a refusal that would need a positive reason.
    assert not admits_paper("not_a_status")
    assert not forbids_durable_evidence("not_a_status")
    assert is_retracted(RETRACTED) and not is_retracted(CLEAR)
    # The rendered predicate names the admitted set, and its negation is the
    # original `<> 'clear'` question.
    rendered = integrity_sql("p")
    negated = integrity_sql("p", admits=False)
    assert rendered == "p.integrity_status IN ('clear')"
    assert negated == "NOT (p.integrity_status IN ('clear'))"
    assert "'clear'" not in negated.replace(rendered, "")
    print(f"statuses={len(INTEGRITY_STATUSES)} sql={rendered}")


if __name__ == "__main__":
    demo()
