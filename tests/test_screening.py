"""The screening predicates and the profile-immutability probe have one owner.

Three vocabularies encode three different defaults on purpose — an undecided paper may hold
an extraction job but must not reach a card. They are preserved here, not unified; what is
shared is their *spelling*, so a value renamed in one site cannot be missed in its siblings.
"""

from __future__ import annotations

import re
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


def test_profile_probe_detects_a_paper_inside_a_topic_profile(tmp_path) -> None:
    """Screening and retrieval are refused once a paper backs a profile result.

    The probe is a join chain; a regression in it would let screening rewrite evidence a
    profile was already built from. Exercise it against a real database rather than
    asserting its text.
    """

    import sqlite3

    from genesis_evidence.core.store.screening import paper_used_by_profile_sql

    connection = sqlite3.connect(tmp_path / "probe.sqlite3")
    connection.executescript(
        """
        CREATE TABLE evidence_profiles(id TEXT PRIMARY KEY, topic_id TEXT);
        CREATE TABLE results(id TEXT PRIMARY KEY);
        CREATE TABLE claims(id TEXT PRIMARY KEY, result_id TEXT, paper_id TEXT);
        CREATE TABLE evidence_profile_results(profile_id TEXT, result_id TEXT);
        """
    )
    connection.executescript(
        """
        INSERT INTO evidence_profiles VALUES ('prof-1', 'topic-1'), ('prof-2', 'topic-2');
        INSERT INTO results VALUES ('res-1');
        INSERT INTO claims VALUES ('claim-1', 'res-1', 'paper-1');
        INSERT INTO evidence_profile_results VALUES ('prof-1', 'res-1');
        """
    )

    found = connection.execute(paper_used_by_profile_sql(), ("topic-1", "paper-1")).fetchone()
    other_topic = connection.execute(paper_used_by_profile_sql(), ("topic-2", "paper-1")).fetchone()
    other_paper = connection.execute(paper_used_by_profile_sql(), ("topic-1", "paper-9")).fetchone()

    assert found is not None, "a paper backing a profile result must be detected"
    assert other_topic is None, "the probe must be scoped to the topic"
    assert other_paper is None, "the probe must be scoped to the paper"


def test_profiled_papers_listing_agrees_with_the_membership_probe(tmp_path) -> None:
    import sqlite3

    from genesis_evidence.core.store.screening import (
        paper_used_by_profile_sql,
        profiled_papers_sql,
    )

    connection = sqlite3.connect(tmp_path / "listing.sqlite3")
    connection.executescript(
        """
        CREATE TABLE evidence_profiles(id TEXT PRIMARY KEY, topic_id TEXT);
        CREATE TABLE results(id TEXT PRIMARY KEY);
        CREATE TABLE claims(id TEXT PRIMARY KEY, result_id TEXT, paper_id TEXT);
        CREATE TABLE evidence_profile_results(profile_id TEXT, result_id TEXT);
        INSERT INTO evidence_profiles VALUES ('prof-1', 'topic-1');
        INSERT INTO results VALUES ('res-1'), ('res-2');
        INSERT INTO claims VALUES ('claim-1', 'res-1', 'paper-1'), ('claim-2', 'res-2', 'paper-2');
        INSERT INTO evidence_profile_results VALUES ('prof-1', 'res-1');
        """
    )

    listed = {
        str(row[0]) for row in connection.execute(profiled_papers_sql(), ("topic-1",)).fetchall()
    }
    assert listed == {"paper-1"}
    # the two forms must agree: listed if and only if the membership probe finds it
    for paper in ("paper-1", "paper-2"):
        probe = connection.execute(paper_used_by_profile_sql(), ("topic-1", paper)).fetchone()
        assert (paper in listed) == (probe is not None), f"forms disagree on {paper}"


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

    # Alias- and line-wrap-agnostic. A copy under a second alias, wrapped across lines,
    # slipped past the literal needles once — match the predicate shape instead.
    flat = " ".join(source.split())
    restated = re.search(
        r"COALESCE\(\s*\w+\.(?:title_abstract|full_text)_decision\s*,\s*'included'\s*\)\s*<>\s*'excluded'",
        flat,
    )
    assert restated is None, f"{path} restates not_excluded inline: {restated and restated.group()}"

    # the immutability walk must not be re-spelled across line breaks either
    assert "FROM evidence_profile_results epr" not in source, f"{path} restates a profile probe"
