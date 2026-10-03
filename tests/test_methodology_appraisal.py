"""Guards for the appraisal map: `core.methodology`'s design→instrument knowledge.

The map was two tables in two shapes: the review gate enforced (design, instrument)
compatibility from one, the review *suggestion* was seeded from another. They agreed
only by inspection — every probe the suggestion could emit happened to be permitted,
an invariant nothing enforced. These tests enforce it.
"""

from __future__ import annotations

from typing import get_args

import pytest

from genesis_evidence.core.methodology import (
    DEFAULT_RISK_OF_BIAS_TOOL,
    RANDOMIZED_DESIGNS,
    RISK_OF_BIAS_TOOLS,
    STUDY_DESIGNS,
    SYNTHESIS_DESIGNS,
    RiskOfBiasTool,
    is_single_primary_capped,
    permitted_risk_of_bias_tools,
    risk_of_bias_tool_for,
)


def test_risk_of_bias_type_is_derived_from_the_one_vocabulary() -> None:
    assert get_args(RiskOfBiasTool) == RISK_OF_BIAS_TOOLS


def test_every_design_declares_its_default_instrument() -> None:
    """The default used to be a ``dict.get(design, "other")`` fallback.

    A fallback is a decision nothing wrote down, and nothing checked. Every design is a
    key now, so adding a design forces the choice instead of silently proposing `other`.
    """

    assert set(DEFAULT_RISK_OF_BIAS_TOOL) == set(STUDY_DESIGNS)


@pytest.mark.parametrize("design", STUDY_DESIGNS)
def test_the_proposed_instrument_is_one_the_gate_permits(design: str) -> None:
    """The invariant the two old tables only satisfied by accident.

    The suggestion seeds a review with one instrument; the gate then accepts or rejects
    it. If the seed is outside the permitted set, the suggestion the operator is shown
    is one the same system will refuse — so this must hold for every design, not the
    eleven that happened to line up.
    """

    permitted = permitted_risk_of_bias_tools(design)
    if permitted is None:
        # No compatibility rule: the gate accepts any instrument, so any seed is fine.
        return
    assert risk_of_bias_tool_for(design) in permitted


def test_permitted_sets_never_name_an_unknown_instrument() -> None:
    for design in STUDY_DESIGNS:
        permitted = permitted_risk_of_bias_tools(design)
        if permitted is not None:
            assert permitted <= set(RISK_OF_BIAS_TOOLS), f"{design}: {permitted}"


def test_designs_without_a_rule_report_no_rule_rather_than_an_empty_set() -> None:
    """``None`` and ``frozenset()`` mean opposite things to the gate.

    The gate skips the check when it gets ``None``; an empty set would reject every
    instrument. A design outside the table must keep the skip behaviour.
    """

    assert permitted_risk_of_bias_tools("guideline") is None
    assert permitted_risk_of_bias_tools("uncertain") is None


def test_risk_of_bias_tool_for_falls_back_only_outside_the_vocabulary() -> None:
    assert risk_of_bias_tool_for("not_a_design") == "other"
    # And never for a design the vocabulary knows.
    assert all(risk_of_bias_tool_for(design) in RISK_OF_BIAS_TOOLS for design in STUDY_DESIGNS)


def test_grade_classes_name_real_designs() -> None:
    assert set(STUDY_DESIGNS) >= RANDOMIZED_DESIGNS
    assert set(STUDY_DESIGNS) >= SYNTHESIS_DESIGNS


def test_a_single_primary_study_is_capped_unless_it_synthesises() -> None:
    """The cap counts papers, not distinct designs.

    One paper of one design is not one paper that synthesises others, and conflating
    the two would drop the cap (or apply it) in the wrong case.
    """

    assert is_single_primary_capped(1, {"cohort_study"}) is True
    assert is_single_primary_capped(1, {"systematic_review_meta_analysis"}) is False
    # Two papers of the same design is not a single primary study.
    assert is_single_primary_capped(2, {"cohort_study"}) is False
    # One synthesis paper alongside another paper is not one either.
    assert is_single_primary_capped(2, {"cohort_study", "systematic_review_meta_analysis"}) is False
