"""One owner of the rule "an evidence change retires the cards that cite it".

Only ``status = 'published'`` cards reach a patient
(``EvidenceStore._published_cards``), so every write site that changes evidence is
load-bearing. Before this module the same predicate was written out in ten places across
``review.py``, ``papers.py``, and the one-time migrations, each with its own copy of the
status set — so a correction could land in one site and silently miss another, leaving a
card published after its paper was retracted.

The three status sets below are the whole rule. Every retirement site builds its predicate
from them, so changing what counts as retireable is one edit here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ..card_lifecycle import NON_TERMINAL_STATUSES

# Propagation: a claim or a paper changed, so every card citing it leaves the pool —
# including cards a patient can currently see. This is exactly the non-terminal
# set: a card already `rejected` or `stale` has left the pool and is not retired
# again. Deriving it means a status added to the vocabulary is retireable without
# a second edit here.
RETIREABLE_STATUSES: tuple[str, ...] = tuple(sorted(NON_TERMINAL_STATUSES))

# Draft-time supersession: a new card at the same condition + scope replaces the
# non-terminal predecessors. A published card is NOT retired here — it leaves the pool when
# it is itself transitioned, which is why this set is narrower than RETIREABLE_STATUSES.
SUPERSEDABLE_STATUSES: tuple[str, ...] = ("draft", "in_review", "approved")

# Publish-time supersession: publishing a card retires the *other published* cards at the
# same scope. Disjoint from SUPERSEDABLE_STATUSES on purpose.
PUBLISHED_STATUSES: tuple[str, ...] = ("published",)


def _in(count: int) -> str:
    return ", ".join("?" * count)


def retire_cards_for_claims(
    connection: sqlite3.Connection, claim_ids: Sequence[str]
) -> int:
    """Retire every card citing any of ``claim_ids``. Returns the number retired."""

    wanted = tuple(str(claim_id) for claim_id in claim_ids)
    if not wanted:
        return 0
    return connection.execute(
        f"""
        UPDATE knowledge_cards SET status = 'stale'
        WHERE status IN ({_in(len(RETIREABLE_STATUSES))}) AND id IN (
            SELECT card_id FROM card_claims WHERE claim_id IN ({_in(len(wanted))})
        )
        """,
        (*RETIREABLE_STATUSES, *wanted),
    ).rowcount


def retire_cards_for_paper(connection: sqlite3.Connection, paper_id: str) -> int:
    """Retire every card citing a claim drawn from ``paper_id``.

    The paper-level twin of :func:`retire_cards_for_claims`, shared by the claim-review,
    admission, integrity, and legacy-split sites.
    """

    return connection.execute(
        f"""
        UPDATE knowledge_cards SET status = 'stale'
        WHERE status IN ({_in(len(RETIREABLE_STATUSES))}) AND id IN (
            SELECT cc.card_id FROM card_claims cc
            JOIN claims c ON c.id = cc.claim_id WHERE c.paper_id = ?
        )
        """,
        (*RETIREABLE_STATUSES, paper_id),
    ).rowcount


def retire_ungoverned_profile_cards(connection: sqlite3.Connection) -> int:
    """Retire published cards whose evidence profile has no governed topic.

    A one-time migration rule: a card that cannot name the topic it belongs to was
    published before topics governed cards, and must not keep serving a patient.
    """

    return connection.execute(
        f"""
        UPDATE knowledge_cards SET status = 'stale'
        WHERE status IN ({_in(len(PUBLISHED_STATUSES))}) AND (
            evidence_profile_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM evidence_profiles ep
                WHERE ep.id = knowledge_cards.evidence_profile_id AND ep.topic_id IS NOT NULL
            )
        )
        """,
        PUBLISHED_STATUSES,
    ).rowcount
