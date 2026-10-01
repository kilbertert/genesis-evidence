"""The retirement rule table has one owner, and every site derives its predicate from it.

`EvidenceStore._published_cards` serves only `status = 'published'` cards, so a site whose
status set drifts from the shared one either keeps a retracted card in front of a patient or
silently removes evidence that should have stayed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from genesis_evidence.core.store.retirement import (
    PUBLISHED_STATUSES,
    RETIREABLE_STATUSES,
    SUPERSEDABLE_STATUSES,
    retire_cards_for_claims,
    retire_cards_for_paper,
)

STORE_DIR = Path(__file__).resolve().parents[1] / "src" / "genesis_evidence" / "core" / "store"


def test_retireable_set_reaches_published_cards() -> None:
    # The whole point: a patient-visible card must be reachable by the propagation rule.
    assert "published" in RETIREABLE_STATUSES
    assert set(RETIREABLE_STATUSES) == {"draft", "in_review", "approved", "published"}


def test_the_three_status_sets_have_the_documented_relationship() -> None:
    assert set(SUPERSEDABLE_STATUSES) == set(RETIREABLE_STATUSES) - set(PUBLISHED_STATUSES)
    assert set(PUBLISHED_STATUSES) == {"published"}


@pytest.mark.parametrize(
    "path",
    sorted(p.name for p in STORE_DIR.glob("*.py") if p.name != "retirement.py"),
)
def test_no_store_restates_a_retirement_status_set_inline(path: str) -> None:
    """A literal status set in a `SET status = 'stale'` site means the rule was copied again."""

    source = (STORE_DIR / path).read_text()
    stale_updates = re.findall(r"SET status = 'stale'.*?(?:\"\"\"|'''|\))", source, re.DOTALL)
    for statement in stale_updates:
        assert "IN ('draft'" not in statement, f"{path} hard-codes the retireable status set"
        assert "= 'published' AND" not in statement, f"{path} hard-codes the published status set"


def test_claim_and_paper_propagation_agree_on_the_same_evidence(tmp_path) -> None:
    import sqlite3

    connection = sqlite3.connect(tmp_path / "cards.sqlite3")
    connection.executescript(
        """
        CREATE TABLE claims(id TEXT PRIMARY KEY, paper_id TEXT);
        CREATE TABLE card_claims(card_id TEXT, claim_id TEXT);
        CREATE TABLE knowledge_cards(id TEXT PRIMARY KEY, status TEXT);
        """
    )
    connection.executemany(
        "INSERT INTO claims(id, paper_id) VALUES (?, ?)", [("c1", "p1"), ("c2", "p2")]
    )
    connection.executemany(
        "INSERT INTO card_claims(card_id, claim_id) VALUES (?, ?)",
        [("k1", "c1"), ("k2", "c2")],
    )
    connection.executemany(
        "INSERT INTO knowledge_cards(id, status) VALUES (?, ?)",
        [("k1", "published"), ("k2", "published")],
    )

    assert retire_cards_for_claims(connection, ("c1",)) == 1
    assert retire_cards_for_paper(connection, "p2") == 1
    remaining = connection.execute("SELECT status FROM knowledge_cards").fetchall()

    assert remaining == [("stale",), ("stale",)]
