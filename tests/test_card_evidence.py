"""The card-eligibility criteria have one owner, and every site interpolates them.

`create_card`, `_require_publishable`, the coverage matrix, and the scope-candidate
projection all decide "may this evidence support a patient-visible card". A criterion that
drifts between them is patient-visible in both directions: a card can be drafted, shown as
ready to publish, or blocked from identical evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_evidence.core.store.card_evidence import (
    CARD_CLAIM_TYPE,
    EXCLUDED_STUDY_DESIGNS,
    UNRESOLVED_RISK_LEVELS,
    sql_values,
)

STORE_DIR = Path(__file__).resolve().parents[1] / "src" / "genesis_evidence" / "core" / "store"


def test_excluded_designs_name_mechanism_and_single_case_designs() -> None:
    assert set(EXCLUDED_STUDY_DESIGNS) == {
        "animal_study",
        "in_vitro_study",
        "case_series",
        "case_report",
    }


def test_rendered_sql_matches_the_literals_it_replaced() -> None:
    # If this changes, every eligibility site changed with it — which is the point.
    assert sql_values(EXCLUDED_STUDY_DESIGNS) == (
        "'animal_study', 'in_vitro_study', 'case_series', 'case_report'"
    )
    assert sql_values(UNRESOLVED_RISK_LEVELS) == "'high', 'critical', 'uncertain'"
    assert CARD_CLAIM_TYPE == "intervention_effect"


@pytest.mark.parametrize(
    "path",
    sorted(p.name for p in STORE_DIR.glob("*.py") if p.name != "card_evidence.py"),
)
def test_no_store_restates_a_card_eligibility_criterion_inline(path: str) -> None:
    """A second copy of a criterion is exactly the drift this module exists to stop.

    The needles are the eligibility *shapes*, not the study-design vocabulary: a schema
    column CHECK or a migration enumerates the designs too, and is not a second copy of the
    rule.
    """

    source = (STORE_DIR / path).read_text()
    # The exact contiguous tuples the rule uses. The schema's column CHECK and the
    # migration enumerate the same design *vocabulary* but never this four-design run, so
    # matching the run does not flag them.
    assert sql_values(EXCLUDED_STUDY_DESIGNS) not in source, (
        f"{path} restates EXCLUDED_STUDY_DESIGNS inline"
    )
    assert "in {'animal_study', 'in_vitro_study', 'case_series', 'case_report'}" not in source, (
        f"{path} restates EXCLUDED_STUDY_DESIGNS as a Python set"
    )
    risk_as_sql = "'high', 'critical', 'uncertain'"
    risk_as_set = "{'high', 'critical', 'uncertain'}"
    assert risk_as_sql not in source, f"{path} restates UNRESOLVED_RISK_LEVELS"
    assert risk_as_set not in source, f"{path} restates UNRESOLVED_RISK_LEVELS"
    assert "= 'intervention_effect'" not in source, f"{path} restates CARD_CLAIM_TYPE in SQL"
    assert "<> 'intervention_effect'" not in source, f"{path} restates CARD_CLAIM_TYPE in SQL"
    assert '!= "intervention_effect"' not in source, f"{path} restates CARD_CLAIM_TYPE"


def test_sql_values_escapes_and_quotes_a_tuple() -> None:
    assert sql_values(("a", "b'c")) == "'a', 'b''c'"
