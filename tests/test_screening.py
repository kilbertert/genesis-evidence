"""The screening predicates and the profile-immutability probe have one owner.

Three vocabularies encode three different defaults on purpose — an undecided paper may hold
an extraction job but must not reach a card. They are preserved here, not unified; what is
shared is their *spelling*, so a value renamed in one site cannot be missed in its siblings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_evidence.core.store.screening import (
    EXCLUDED,
    INCLUDED,
    excluded_any_stage_sql,
    included_sql,
    not_excluded_sql,
)

STORE_DIR = Path(__file__).resolve().parents[1] / "src" / "genesis_evidence" / "core" / "store"


def test_the_three_vocabularies_keep_their_different_defaults() -> None:
    """Unifying these would change which papers are extracted or published. Pin them apart."""

    column = "cp.full_text_decision"
    assert included_sql(column) == f"{column} = 'included'"
    assert not_excluded_sql(column) == f"COALESCE({column}, 'included') <> 'excluded'"

    # The disagreement, stated: an undecided decision is ineligible under `included` and
    # eligible under `not_excluded`.
    assert "'included'" in included_sql(column)
    assert "COALESCE" in not_excluded_sql(column)


def test_excluded_any_collapses_stages_not_values() -> None:
    assert excluded_any_stage_sql("cp.full_text_decision", "cp.title_abstract_decision") == (
        "COALESCE(cp.full_text_decision, cp.title_abstract_decision) = 'excluded'"
    )


def test_decision_constants_are_the_words_the_sql_uses() -> None:
    assert (INCLUDED, EXCLUDED) == ("included", "excluded")


@pytest.mark.parametrize(
    "path",
    sorted(p.name for p in STORE_DIR.glob("*.py") if p.name != "screening.py"),
)
def test_no_store_restates_a_screening_predicate_inline(path: str) -> None:
    source = (STORE_DIR / path).read_text()
    forbidden = {
        "full_text_decision = 'included'": "the included predicate",
        "item.full_text_decision = 'included'": "included",
        "COALESCE(cp.full_text_decision, 'included') <> 'excluded'": "not_excluded",
        "COALESCE(cp.title_abstract_decision, 'included') <> 'excluded'": "not_excluded",
        "COALESCE(cp.full_text_decision, cp.title_abstract_decision) = 'excluded'": "excluded_any",
    }
    for needle, name in forbidden.items():
        assert needle not in source, f"{path} restates {name} inline"
