"""Guards for the one publication-integrity owner: `core.publication_integrity` (#264).

One body of knowledge — *what a paper's publication integrity is, which statuses admit a
paper, and which statuses forbid durable evidence* — was reproduced as a typed enum, a
SQL ``CHECK``, a validation ``set``, three write-side refusals, three acquisition-side
checks, and seven SQL gates plus one ``CASE`` branch. The admissions rule was written as
an equality in six places and an inequality in two, with no single statement of what the
two spellings had in common.

The schema ``CHECK`` cannot import Python and stays literal; these tests are what keeps
it from drifting. Each guard fails on the shape it forbids, not merely passes today.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from genesis_evidence.core.publication_integrity import (
    ADMISSIBLE_STATUSES,
    CLEAR,
    FORBIDS_DURABLE_EVIDENCE,
    INTEGRITY_STATUSES,
    RETRACTED,
    UNKNOWN,
    IntegrityStatusValue,
    admits_paper,
    forbids_durable_evidence,
    integrity_sql,
    is_retracted,
)
from genesis_evidence.literature.integrity import IntegrityStatus

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "genesis_evidence"


def _source(relative_path: str) -> str:
    return (SOURCE_ROOT / relative_path).read_text()


def _sql_integrity_check() -> set[str]:
    """The value set of `papers.integrity_status`'s CHECK, whitespace-insensitively."""

    source = _source("core/store/schema.py")
    match = re.search(
        r"integrity_status\s+TEXT[^,]*?CHECK\s*\(integrity_status\s+IN\s*\((.*?)\)\)",
        source,
        re.DOTALL,
    )
    assert match, "schema.py: papers.integrity_status has no CHECK"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_status_type_is_derived_from_the_one_vocabulary() -> None:
    assert get_args(IntegrityStatusValue) == INTEGRITY_STATUSES


def test_the_provider_enum_is_a_checked_view_of_the_one_vocabulary() -> None:
    """Redeclaring the members is intentional; drifting from them is not."""

    assert tuple(member.value for member in IntegrityStatus) == INTEGRITY_STATUSES
    assert IntegrityStatus.CLEAR.value == CLEAR
    assert IntegrityStatus.RETRACTED.value == RETRACTED


def test_the_schema_check_matches_the_vocabulary() -> None:
    """A schema string cannot import Python, so a guard is what keeps it honest."""

    values = _sql_integrity_check()
    assert len(values) == len(INTEGRITY_STATUSES), f"extracted {values}"
    assert values == set(INTEGRITY_STATUSES), "papers.integrity_status CHECK has drifted"


def test_the_members_the_code_singles_out_are_members() -> None:
    assert {CLEAR, RETRACTED, UNKNOWN} <= set(INTEGRITY_STATUSES)
    assert UNKNOWN == "unknown"


def test_admission_and_forbiddenness_are_different_questions() -> None:
    """`corrected` admits no paper and forbids no evidence; `retracted` does both
    only in the forbidden direction. Neither set is the other's complement."""

    assert not (ADMISSIBLE_STATUSES & FORBIDS_DURABLE_EVIDENCE)
    assert set(INTEGRITY_STATUSES) >= ADMISSIBLE_STATUSES
    assert set(INTEGRITY_STATUSES) >= FORBIDS_DURABLE_EVIDENCE
    # A corrected paper is not admitted and not forbidden: the two questions really
    # are separate, and this is the case that pins it.
    assert not admits_paper("corrected")
    assert not forbids_durable_evidence("corrected")


def test_the_predicates_fail_closed_on_an_admission_and_open_on_a_refusal() -> None:
    """An unknown status must never be admitted; it also must not be called
    retracted, because a refusal needs a positive reason."""

    assert not admits_paper("not_a_status")
    assert not forbids_durable_evidence("not_a_status")
    assert is_retracted(RETRACTED) and not is_retracted(CLEAR)


def test_the_rendered_sql_agrees_with_the_predicate_for_every_status() -> None:
    rendered = integrity_sql("p")
    negated = integrity_sql("p", admits=False)
    for status in INTEGRITY_STATUSES:
        assert (f"'{status}'" in rendered) is admits_paper(status), status
        assert (f"'{status}'" in negated) is admits_paper(status), status
    assert rendered.startswith("p.integrity_status IN (")
    assert negated == f"NOT ({rendered})"


#: The copy this forbids: a comparison against the raw column drawing a conclusion
#: about one status. `review.py` used `!= "clear"` in six places and `<> 'clear'` in
#: two; `papers.py` used `== "retracted"` in three. Any of those separators, in either
#: quote style, and with or without a parenthesis before the value, is the rule
#: restated instead of rendered. Case-insensitive because SQL spells the operator
#: `IN` and Python spells the column in whatever case the surrounding code uses.
_RESTATED_RULE = re.compile(
    r"""integrity_status\s*
        (?: ['\"]\s*\] )?                     # subscript: ["integrity_status"]
        \s*
        (?: == | != | <> | = | \bin\b | \bnot\s+in\b )
        \s* \(? \s* ['\"](?:clear|retracted)['\"]""",
    re.VERBOSE | re.IGNORECASE,
)

#: `publication_integrity.py` defines the rule and renders it, so its own source
#: legitimately names the statuses. `schema.py` is an *enumeration* of the vocabulary
#: in a string that cannot import Python — a different statement from a comparison
#: that concludes something about one status — and it is pinned separately by
#: `test_the_schema_check_matches_the_vocabulary`.
_RULE_SCAN_EXCLUDED = {"publication_integrity.py", "schema.py"}


def test_no_module_restates_the_admission_rule() -> None:
    """The one allowed spelling is a call to `integrity_sql` or `admits_paper`."""

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name in _RULE_SCAN_EXCLUDED:
            continue
        for match in _RESTATED_RULE.finditer(path.read_text()):
            offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {match.group(0).strip()}")
    assert not offenders, "admission rule restated:\n" + "\n".join(offenders)


def test_the_rule_scan_actually_catches_a_restatement() -> None:
    """A guard that cannot fail is not a guard."""

    def emitted(text: str) -> bool:
        return bool(_RESTATED_RULE.search(text))

    # The Python copies that were actually there.
    assert emitted('if paper["integrity_status"] != "clear":')
    assert emitted("or row['integrity_status'] != 'clear'")
    assert emitted('if paper["integrity_status"] == "retracted":')
    assert emitted("if status_row['integrity_status'] == 'retracted':")
    # The SQL copies that were actually there.
    assert emitted("WHERE p.integrity_status = 'clear'")
    assert emitted("WHEN p.integrity_status <> 'clear' THEN 'blocked'")
    assert emitted("OR p.integrity_status <> 'clear'")
    assert emitted("p.integrity_status IN ('clear')")
    # The rendered form, and the predicate call, are not restatements.
    assert not emitted('WHERE {integrity_sql("p", admits=False)}')
    assert not emitted('if not admits_paper(str(row["integrity_status"])):')
    # A *different* value class that shares the word is not this rule.
    assert not emitted("extraction_status = 'clear'")
    assert not emitted('extraction_status: Literal["clear", "ambiguous"]')


def test_no_module_restates_the_vocabulary_as_a_literal_or_a_bare_set() -> None:
    """Three or more statuses in one span is a second copy of the vocabulary."""

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "publication_integrity.py":
            continue
        text = path.read_text()
        for match in re.finditer(r"Literal\[([^\]]*)\]", text):
            hits = [
                status
                for status in INTEGRITY_STATUSES
                if re.search(rf"['\"]{re.escape(status)}['\"]", match.group(1))
            ]
            if len(hits) >= 3:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: Literal {sorted(hits)}")
        for match in re.finditer(r"\{([^{}]*)\}", text):
            values = {item.strip().strip("'\"") for item in match.group(1).split(",")}
            if len(values & set(INTEGRITY_STATUSES)) >= 3:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: set {sorted(values)}")
    assert not offenders, "integrity vocabulary restated:\n" + "\n".join(offenders)


def test_the_vocabulary_scan_actually_catches_a_restatement() -> None:
    def flags_literal(text: str) -> bool:
        for match in re.finditer(r"Literal\[([^\]]*)\]", text):
            hits = [
                status
                for status in INTEGRITY_STATUSES
                if re.search(rf"['\"]{re.escape(status)}['\"]", match.group(1))
            ]
            if len(hits) >= 3:
                return True
        return False

    def flags_set(text: str) -> bool:
        for match in re.finditer(r"\{([^{}]*)\}", text):
            values = {item.strip().strip("'\"") for item in match.group(1).split(",")}
            if len(values & set(INTEGRITY_STATUSES)) >= 3:
                return True
        return False

    assert flags_literal('x: Literal["clear", "updated", "corrected", "retracted"]')
    assert flags_set('STATUSES = {"clear", "updated", "corrected", "retracted", "unknown"}')
    # A one- or two-value narrowing is a legitimate shape elsewhere.
    assert not flags_literal('x: Literal["clear", "retracted"]')
    assert not flags_set('{"clear": 0, "ambiguous": 1}')
    # A differently-named value class sharing the word is not this vocabulary.
    assert not flags_literal('extraction_status: Literal["clear", "ambiguous"]')


def test_the_store_imports_the_vocabulary_rather_than_spelling_it() -> None:
    source = _source("core/store/papers.py")
    assert "INTEGRITY_STATUSES" in source
    assert "forbids_durable_evidence" in source
    # The validation set and the three refusals are gone from the store.
    assert 'if status not in {\n            "clear",' not in source
    assert '== "retracted"' not in source


def test_every_store_write_path_asks_the_one_predicate() -> None:
    """The three refusals `PaperStore.integrity_status`'s docstring promised."""

    source = _source("core/store/papers.py")
    # save_full_text, enqueue_extraction, save_ai_extraction.
    assert source.count("forbids_durable_evidence(") == 3


def test_a_retracted_paper_is_refused_on_every_store_write_path(tmp_path) -> None:
    """The case the docstring claimed and no store path exercised before."""

    import pytest

    from genesis_evidence.core.store import Database, PaperStore
    from genesis_evidence.core.store.papers import PaperRetracted, StoredObject

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, integrity_status, created_at) "
            "VALUES ('paper-1', 'Retracted paper', 'retracted', 'now')"
        )

    assert store.integrity_status("paper-1") == RETRACTED
    stored = StoredObject(key="k", sha256="0" * 64, size=0)
    with pytest.raises(PaperRetracted, match="retracted paper"):
        store.save_full_text(
            "paper-1", stored, media_type="text/plain", rights_status="redistributable"
        )
    with pytest.raises(PaperRetracted, match="retracted paper"):
        store.enqueue_extraction("paper-1", collection_run_id=None)


def test_an_admissible_paper_is_not_refused(tmp_path) -> None:
    """The control: the refusal is about the status, not the write path itself."""

    from genesis_evidence.core.store import Database, PaperStore

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, integrity_status, created_at) "
            "VALUES ('paper-1', 'Clean paper', 'clear', 'now')"
        )
    assert store.integrity_status("paper-1") == CLEAR
    assert admits_paper(store.integrity_status("paper-1"))


def test_the_acquisition_backstop_asks_the_one_predicate() -> None:
    """It called `PaperStore.integrity_status`, whose docstring promised exactly this."""

    source = _source("literature/ingestion.py")
    assert "forbids_durable_evidence(self._store.integrity_status(paper_id))" in source
    assert "IntegrityStatus.RETRACTED.value" not in source


def test_the_review_store_renders_every_gate_rather_than_spelling_it() -> None:
    source = _source("core/store/review.py")
    assert "integrity_sql(" in source
    assert "admits_paper(" in source
    # The seven SQL gates and the queue CASE render their predicate from the
    # module. The scan above is the real guard; this pins that the store's own
    # query sites actually switched rather than only the Python branches.
    assert source.count("integrity_sql(") == 5
