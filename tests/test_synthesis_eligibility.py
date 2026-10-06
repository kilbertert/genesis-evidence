"""A component name is not sufficient: the recorded intervention decides (ADR 0007)."""

from genesis_evidence.core.synthesis_eligibility import (
    EXCLUDED_INTERVENTIONS,
    is_component_poolable,
)


def test_the_recorded_fortified_milk_is_not_poolable_as_vitamin_d() -> None:
    """The case the module exists for, verbatim from the 2026-10-06 database."""

    assert not is_component_poolable(
        "Vitamin D",
        "Iron and vitamin D fortified flavored skim milk (500 mL, containing "
        "15 mg of iron and 5 µg of vitamin D)",
    )


def test_an_ordinary_vitamin_d_result_still_pools() -> None:
    """The control: the exclusion must not swallow the whole component."""

    for form in ("Oral supplement", "未报告", "Cholecalciferol 1000 IU daily", ""):
        assert is_component_poolable("Vitamin D", form), form


def test_the_exclusion_is_scoped_to_its_component() -> None:
    # A different component whose form happens to mention iron is unaffected.
    assert is_component_poolable("Olive oil", "Olive oil with iron fortification")


def test_every_recorded_exclusion_carries_a_reason() -> None:
    for excluded in EXCLUDED_INTERVENTIONS:
        assert excluded.ingredient_name and excluded.form_marker
        assert len(excluded.reason) > 20, excluded
