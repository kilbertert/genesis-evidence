"""Guards for the one evidence-appraisal taxonomy: `core.methodology`.

The taxonomy was restated six times before this module owned it — two Python
``Literal``s, two SQL ``CHECK``s, and two browser lists. Two of those cannot import
Python at all, so they stay literal; these tests are what keeps them from drifting.
Each guard is written to fail on the shape it forbids, not merely to pass today.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from genesis_evidence.core.methodology import (
    OBSERVATIONAL_DESIGNS,
    STUDY_DESIGNS,
    StudyDesign,
    permits_causal_inference,
)
from genesis_evidence.core.store.card_evidence import EXCLUDED_STUDY_DESIGNS

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "genesis_evidence"

def _sql_check_values(relative_path: str) -> set[str]:
    """The value set of a `corrected_study_design` CHECK, whitespace-insensitively.

    ``schema.py`` and ``database.py`` wrap the same 18 values at different columns,
    so the extraction must not depend on line breaks.
    """

    source = (SOURCE_ROOT / relative_path).read_text()
    match = re.search(r"corrected_study_design\s+IN\s*\((.*?)\)", source, re.DOTALL)
    assert match, f"{relative_path}: no corrected_study_design CHECK found"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def _workbench_source() -> str:
    return (SOURCE_ROOT / "review" / "workbench.html").read_text()


def test_study_design_type_is_derived_from_the_one_vocabulary() -> None:
    """The derived type must be the vocabulary's own arguments, not a lookalike.

    This states the relation, but it cannot *detect* a second hand-written ``Literal``
    that happens to hold the same values — only the restatement scan below can, because
    equality of contents is exactly what such a copy would satisfy.
    """

    assert get_args(StudyDesign) == STUDY_DESIGNS


def test_no_module_restates_the_design_vocabulary_in_a_literal() -> None:
    """The scan that actually catches a re-declared vocabulary.

    ``Literal[...]`` is the shape every restatement of the design vocabulary took, so a
    span holding several design names is a second copy regardless of whether it matches
    today. The bar is deliberately low: a copy that has *already* drifted is the case
    content-equality checks cannot see either.

    Names are matched in either quote style. The repository prefers double quotes, but
    the linter does not enforce it, so a single-quoted copy is a real spelling and must
    not be the way past the guard.

    This is a source-level scan, not a proof. A copy expressed as a bare list, a set, or
    a mapping would evade it — but those shapes carry other meanings here
    (``card_evidence.EXCLUDED_STUDY_DESIGNS`` and the design→instrument maps are
    criteria and tables, not the vocabulary), so they are not restatements to forbid.
    """

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        for match in re.finditer(r"Literal\[([^\]]*)\]", path.read_text()):
            hits = [
                design
                for design in STUDY_DESIGNS
                if re.search(rf"['\"]{re.escape(design)}['\"]", match.group(1))
            ]
            if len(hits) >= 3:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {len(hits)} design names")
    assert not offenders, "study-design vocabulary restated as a Literal: " + "; ".join(offenders)


def test_the_vocabulary_has_no_duplicates() -> None:
    """A repeated value would make the design count lie while the sets still match."""

    assert len(set(STUDY_DESIGNS)) == len(STUDY_DESIGNS)


@pytest.mark.parametrize(
    "relative_path",
    ["core/store/schema.py", "core/store/database.py"],
)
def test_the_sql_check_matches_the_vocabulary(relative_path: str) -> None:
    """A schema string cannot import Python, so a guard is what keeps it honest."""

    values = _sql_check_values(relative_path)
    # A regex that silently stops matching would make the comparison below vacuous.
    assert len(values) == len(STUDY_DESIGNS), f"{relative_path}: extracted {values}"
    assert values == set(STUDY_DESIGNS), f"{relative_path} CHECK has drifted"


def test_the_workbench_design_list_matches_the_vocabulary_as_a_set() -> None:
    """The browser cannot import Python either. Order is a deliberate UI grouping."""

    match = re.search(r"const designs = \[(.*?)\];", _workbench_source())
    assert match, "workbench.html: no `const designs` array found"
    values = re.findall(r"'([a-z_]+)'", match.group(1))
    assert len(values) == len(STUDY_DESIGNS), f"workbench design list has {len(values)} entries"
    assert set(values) == set(STUDY_DESIGNS), "workbench design list has drifted"


def test_the_workbench_display_names_cover_every_selectable_design() -> None:
    """A design with no display name renders as its raw token in the disease library."""

    match = re.search(r"const studyDesignNames = \{(.*?)\};", _workbench_source())
    assert match, "workbench.html: no studyDesignNames map found"
    names = set(re.findall(r"([a-z_]+):", match.group(1)))
    unknown = names - set(STUDY_DESIGNS)
    assert not unknown, f"display names for unknown designs: {unknown}"
    # `uncertain` is the extraction model's "could not tell" and is not offered as a
    # reviewable design, so it legitimately has no display name.
    assert set(STUDY_DESIGNS) - {"uncertain"} <= names, (
        f"designs with no display name: {set(STUDY_DESIGNS) - {'uncertain'} - names}"
    )


def test_the_card_eligibility_exclusion_set_names_real_designs() -> None:
    """#139 owns *which designs are ineligible*; this module owns the vocabulary.

    Two owners over one vocabulary is only safe if the relation is checkable.
    """

    assert set(EXCLUDED_STUDY_DESIGNS) <= set(STUDY_DESIGNS)


def test_only_observational_designs_forbid_a_causal_claim() -> None:
    for design in STUDY_DESIGNS:
        assert permits_causal_inference(design) is (design not in OBSERVATIONAL_DESIGNS)


def test_the_observational_set_is_the_recorded_six() -> None:
    assert {
        "cohort_study",
        "case_control_study",
        "cross_sectional_study",
        "case_series",
        "case_report",
        "ecological_study",
    } == OBSERVATIONAL_DESIGNS
