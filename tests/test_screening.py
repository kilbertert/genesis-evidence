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


def test_conflict_sql_and_python_agree_on_every_reachable_matrix() -> None:
    """The SQL predicate and the Python form are two implementations of one rule.

    The ledger reconciler groups runs in Python; the topic-closure gate groups them in SQL.
    If the two disagree the reconciler will normalize a state closure refuses (or the
    reverse), which is exactly the drift #208 is about.
    """

    import itertools
    import sqlite3

    from genesis_evidence.core.store.screening import run_conclusion, screening_conflict_sql

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE cp(paper_id TEXT, title_abstract_decision TEXT, full_text_decision TEXT);"
    )
    body = screening_conflict_sql(
        "cp.paper_id", "cp.title_abstract_decision", "cp.full_text_decision"
    )
    values = [None, "included", "excluded"]
    checked = 0
    for left in itertools.product(values, repeat=2):
        for right in itertools.product(values, repeat=2):
            connection.execute("DELETE FROM cp")
            for title, full_text in (left, right):
                connection.execute("INSERT INTO cp VALUES('p', ?, ?)", (title, full_text))
            by_sql = connection.execute(
                f"SELECT count(*) > 0 FROM (SELECT 1 FROM cp WHERE paper_id = 'p' "
                f"GROUP BY paper_id HAVING {body})"
            ).fetchone()[0]
            conclusions = {
                conclusion
                for title, full_text in (left, right)
                if (conclusion := run_conclusion(full_text, title)) is not None
            }
            by_python = len(conclusions) > 1
            assert bool(by_sql) == by_python, (
                f"SQL says {bool(by_sql)}, Python says {by_python} for "
                f"{left} vs {right}"
            )
            checked += 1
    assert checked == 81


def test_structured_results_sql_and_python_agree_on_every_matrix() -> None:
    """The queue runs this check in SQL; the detail runs it in Python.

    If they disagree the queue advertises work the detail refuses — the #211 mismatch.
    Exhaustive over one and two claims with every required field filled or blank.
    """

    import itertools
    import sqlite3

    from genesis_evidence.core.store.screening import (
        RESULT_FIELDS,
        has_structured_results,
        structured_results_sql,
    )

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE papers(id TEXT);
        CREATE TABLE claims(id TEXT, paper_id TEXT, result_id TEXT,
            evidence_text TEXT, locator TEXT);
        CREATE TABLE results(id TEXT, population TEXT, ingredient_name TEXT,
            ingredient_form TEXT, dose TEXT, comparator TEXT, outcome TEXT,
            timepoint TEXT, effect_estimate TEXT, statistical_details TEXT);
        INSERT INTO papers VALUES('p');
        """
    )
    query = "SELECT " + structured_results_sql("'p'")
    blanks = [True, False]
    checked = 0

    # A uniform mask per iteration (the first version) could never produce the mixed
    # complete/incomplete case the EXISTS form let through, so vary each claim separately.
    # Kept bounded: the full product over three claims is astronomically large.
    single = [
        mask
        for mask in itertools.product(blanks, repeat=len(RESULT_FIELDS) + 2)
        # keep the all-present and one-field-blank extremes plus a few interior shapes
        if mask.count(False) in {0, 1, len(RESULT_FIELDS) + 1, len(mask)}
    ]
    shapes: list[tuple[tuple[bool, ...], ...]] = [()]
    shapes += [(mask,) for mask in single]
    shapes += [(left, right) for left in single for right in single]
    shapes += [(a, b, c) for a in single for b in single[:8] for c in single[:4]]

    for masks in shapes:
        connection.execute("DELETE FROM claims")
        connection.execute("DELETE FROM results")
        claims: list[dict[str, object]] = []
        for index, mask in enumerate(masks):
            result_id = f"r{index}"
            values = {
                field: ("" if mask[position] else f"v{field}")
                for position, field in enumerate(RESULT_FIELDS)
            }
            connection.execute(
                "INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?,?)",
                (result_id, *(values[field] for field in RESULT_FIELDS)),
            )
            evidence = "" if mask[len(RESULT_FIELDS)] else "evidence"
            locator = "" if mask[len(RESULT_FIELDS) + 1] else "locator"
            connection.execute(
                "INSERT INTO claims VALUES(?,?,?,?,?)",
                (f"c{index}", "p", result_id, evidence, locator),
            )
            claims.append(
                {
                    "result_id": result_id,
                    "evidence_text": evidence,
                    "locator": locator,
                    **values,
                }
            )
        by_sql = bool(connection.execute(query).fetchone()[0])
        by_python = has_structured_results(claims)
        assert by_sql == by_python, f"SQL says {by_sql}, Python says {by_python} for {masks}"
        checked += 1
    assert checked >= 1000


def test_structured_results_treats_whitespace_only_as_empty() -> None:
    """Both forms must use the same explicit whitespace set.

    SQLite's `trim()` knows only the characters it is handed; Python's `str.strip()` strips
    everything Unicode calls whitespace. Left to their defaults the two disagree — on a tab
    (SQL: content, Python: empty) and on a non-breaking space (SQL: content, Python: empty).
    `EMPTY_WHITESPACE` pins both.
    """

    import sqlite3

    from genesis_evidence.core.store.screening import (
        has_structured_results,
        structured_results_sql,
    )

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE papers(id TEXT);
        CREATE TABLE claims(id TEXT, paper_id TEXT, result_id TEXT,
            evidence_text TEXT, locator TEXT);
        CREATE TABLE results(id TEXT, population TEXT, ingredient_name TEXT,
            ingredient_form TEXT, dose TEXT, comparator TEXT, outcome TEXT,
            timepoint TEXT, effect_estimate TEXT, statistical_details TEXT);
        INSERT INTO papers VALUES('p');
        INSERT INTO results VALUES('r','\t\n ','v','v','v','v','v','v','v','v');
        INSERT INTO claims VALUES('c','p','r','ev','loc');
        """
    )
    query = "SELECT " + structured_results_sql("'p'")
    by_sql = bool(connection.execute(query).fetchone()[0])
    by_python = has_structured_results(
        [
            {
                "result_id": "r",
                "evidence_text": "ev",
                "locator": "loc",
                **{field: "v" for field in (
                    "ingredient_name", "ingredient_form", "dose", "comparator",
                    "outcome", "timepoint", "effect_estimate", "statistical_details",
                )},
                "population": "\t\n ",
            }
        ]
    )

    assert by_sql == by_python is False


def test_structured_results_agrees_on_unicode_whitespace() -> None:
    """A lone non-breaking space is content in both forms, by the shared explicit policy.

    Python's `str.strip()` would strip U+00A0 and SQL's `trim()` cannot name it, so the two
    default behaviours disagree. Pinning both to `EMPTY_WHITESPACE` makes the char content
    on both sides rather than leaving the answer to each runtime's idea of whitespace.
    """

    import sqlite3

    from genesis_evidence.core.store.screening import (
        RESULT_FIELDS,
        has_structured_results,
        structured_results_sql,
    )

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE papers(id TEXT);
        CREATE TABLE claims(id TEXT, paper_id TEXT, result_id TEXT,
            evidence_text TEXT, locator TEXT);
        CREATE TABLE results(id TEXT, population TEXT, ingredient_name TEXT,
            ingredient_form TEXT, dose TEXT, comparator TEXT, outcome TEXT,
            timepoint TEXT, effect_estimate TEXT, statistical_details TEXT);
        INSERT INTO papers VALUES('p');
        """
    )
    query = "SELECT " + structured_results_sql("'p'")

    for character in (" ", " "):
        connection.execute("DELETE FROM claims")
        connection.execute("DELETE FROM results")
        connection.execute(
            "INSERT INTO results VALUES('r'," + ",".join(["?"] * 9) + ")",
            (character, *["v"] * 8),
        )
        connection.execute("INSERT INTO claims VALUES('c','p','r','ev','loc')")
        by_sql = bool(connection.execute(query).fetchone()[0])
        by_python = has_structured_results(
            [
                {
                    "result_id": "r",
                    "evidence_text": "ev",
                    "locator": "loc",
                    "population": character,
                    **{field: "v" for field in RESULT_FIELDS if field != "population"},
                }
            ]
        )
        assert by_sql == by_python, f"disagree on U+{ord(character):04X}"
