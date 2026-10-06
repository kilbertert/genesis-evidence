"""One owner of the rule "which result may enter a component pool" (ADR 0007).

Component identity is decided from the intervention **name** (``core.components``),
which is what makes it exact and fail-closed. The name is not always the whole
story, though: a result whose name is a single component can still describe an
intervention that is not only that component. The catalogued vitamin-D name is
the recorded case — one row under it reads

    ingredient_name = 'Vitamin D'
    ingredient_form = 'Iron and vitamin D fortified flavored skim milk (...)'

so pooling it as a vitamin-D body would count an iron intervention towards a
vitamin-D conclusion.

This module is the place that decision lives, and it is deliberately a table of
recorded cases rather than a heuristic over the form text. The form column is
free text with no controlled vocabulary (~680 distinct values across ~672 names
on the 2026-10-06 database), so there is nothing general to match against; a
rule invented here would be exactly the unverified claim ADR 0007 warns against.
What the table buys is that the known exclusions are applied in one place, are
testable, and stay applied if the grouping is ever re-implemented.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True, slots=True)
class ExcludedIntervention:
    """A recorded result whose name understates the intervention studied."""

    ingredient_name: str
    form_signature: str
    reason: str


#: Recorded result-level exclusions. Each is a case where the intervention name
#: resolves to one component but the recorded form shows the study studied
#: something else or something additional, so the result must not pool as that
#: component alone.
#:
#: The signature is a **phrase naming the recorded case**, never a single word.
#: A single word like "iron" would also match "Iron-free oral vitamin D
#: supplement" and drop valid evidence — the opposite failure, and a worse one,
#: because it is silent.
EXCLUDED_INTERVENTIONS: tuple[ExcludedIntervention, ...] = (
    ExcludedIntervention(
        ingredient_name="Vitamin D",
        form_signature="iron and vitamin d fortified",
        reason=(
            "the recorded form is an iron-and-vitamin-D fortified milk, so the "
            "study administered iron as well as vitamin D"
        ),
    ),
    ExcludedIntervention(
        ingredient_name="EVOO",
        form_signature="mediterranean diet",
        reason=(
            "the recorded form places the olive oil within a Mediterranean-diet "
            "trial, so the study's intervention is a dietary pattern rather than "
            "an olive-oil exposure"
        ),
    ),
)


def is_component_poolable(ingredient_name: str, ingredient_form: str) -> bool:
    """Whether a result may be pooled as the component its name resolves to.

    False means "excluded from component synthesis", not "excluded from the
    evidence": the result stays stored, reviewable and approvable, and simply
    joins no component body.
    """

    name = _normalize(ingredient_name)
    form = _normalize(ingredient_form)
    for excluded in EXCLUDED_INTERVENTIONS:
        if _normalize(excluded.ingredient_name) != name:
            continue
        if _normalize(excluded.form_signature) in form:
            return False
    return True


def demo() -> None:
    """Smallest runnable check: the recorded case is excluded, others are not."""

    assert not is_component_poolable(
        "Vitamin D", "Iron and vitamin D fortified flavored skim milk (500 mL)"
    )
    assert not is_component_poolable("Vitamin D", "IRON AND VITAMIN D FORTIFIED MILK")
    # The same name with an ordinary form still pools.
    assert is_component_poolable("Vitamin D", "Oral supplement")
    assert is_component_poolable("Vitamin D", "未报告")
    assert is_component_poolable("Vitamin D", "")
    # A form that names the absence of iron is not the fortified milk.
    assert is_component_poolable("Vitamin D", "Iron-free oral vitamin D supplement")
    # The dietary-pattern EVOO case, and an ordinary EVOO result beside it.
    assert not is_component_poolable("EVOO", "EVOO within Mediterranean Diet")
    assert is_component_poolable("EVOO", "EVOO as the principal fat")
    # Another component is untouched by the vitamin-D exclusion.
    assert is_component_poolable("Olive oil", "Olive oil gel capsules")
    print(f"excluded_interventions={len(EXCLUDED_INTERVENTIONS)} ok")


if __name__ == "__main__":
    demo()
