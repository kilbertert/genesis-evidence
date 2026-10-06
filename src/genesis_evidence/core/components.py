"""Canonical component identities for scientific evidence (ADR 0007).

The grouping axis for scientific evidence is the **component** — a canonical
substance identity plus its exact chemical form — not the disease and not the
paper. This module owns that vocabulary, in the same shape as
`core.conditions` and `core.metrics`: a code-held, version-controlled catalog
with no table of its own.

Resolution is **exact-match-then-fail-closed**. A source string either resolves
to one component form or it does not resolve at all; there is no substring,
prefix, or similarity fallback. That is deliberate: a loose text match is what
let "Calcium, vitamin D, protein, or dietary pattern" pool ten different
interventions under one certainty value, so a near-miss here must produce an
explicit unmapped answer rather than a plausible one.

Two consequences are intended rather than tolerated:

- **Multi-component interventions do not resolve.** A supplement carrying
  several nutrients, a whole dietary pattern, or a commercial product whose
  composition is not a single substance is absent from the catalog. Such a
  result stays in the evidence body and is reviewable and approvable; it is
  simply not *poolable* at component level.
- **Non-nutrient interventions do not resolve either.** The review corpus
  contains strings such as "抗阻运动" and "运动干预" that are exercise
  programmes, not exposures of the kind this catalog names. They are absent by
  construction, so they can never be pooled as if they were a nutrient.

**A class is not a component.** "必需氨基酸" names a set of substances and
"Fiber supplementation" names a nutrient class, so neither identifies what a
study actually administered. They are deliberately **absent**: two studies that
both say "fiber" may have used different fibres, and pooling them would compute
a certainty for no single intervention. Only an entry that names one substance
belongs here.

**`results.ingredient_form` is not a form field, and resolution takes only the
intervention name.** That column holds free text — measured on the 2026-10-06
database it carries 680 distinct values across 672 distinct `ingredient_name`
values, and its contents are dose descriptions, delivery modes and even
questionnaire wording ("750 mL of olive oil for the MD group supply", "How much
olive oil do you consume per day", "口服胶囊"). It cannot discriminate one
chemical form from another. The `form` recorded on a `ComponentForm` is the form
**this catalog declares for that exact name**, not a value read back from a
result.

**What the form field is good for is catching names that are too coarse to be a
component.** Because it *does* describe what was administered, reading it across
the rows that share one name shows when one name spans more than one substance —
and that is how `Vitamin D` and `膳食盐` were removed from this catalog:

- `Vitamin D` — a pure supplement, an unreported form, and an
  iron-and-vitamin-D fortified milk: a family, not a substance.
- `膳食盐` — a salt-reduction *education* programme, no supplement or salt
  named: the name does not describe what was given.

So the field is not read to derive a form; it is read once, by hand, to decide
whether a name deserves an entry at all.

**The test is whether the *name* denotes one substance, not whether every row
carrying it is clean.** `EVOO` stays in the catalog next to `Vitamin D`'s removal
for exactly that reason: "EVOO" names one substance (extra-virgin olive oil) even
though one of its rows records a Mediterranean-diet context, whereas "Vitamin D"
names a family (D2, D3, calcidiol) with no single substance behind it. A name
that a *study* used loosely is a data-quality problem for that study, and it is
handled where that study's data is — by the per-result synthesis-eligibility
predicate, which can read the `ingredient_form` text and reject that one result.
Judging by the noisiest row would let one row remove a real component from every
other study that used the name correctly.

The catalog is intentionally small and is expected to grow. Growth must be by
adding a line for a **newly named, single, determinate component**, not by
loosening the match.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

#: A component key names a substance; a form names its exact chemical form or
#: provenance. Both together are the pooling identity, so two results pool only
#: when both agree.
SINGLE_FORM = ""


@dataclass(frozen=True, slots=True)
class ComponentForm:
    """One resolved component identity: a substance and, optionally, its form."""

    component_key: str
    form: str
    label: str

    @property
    def pooled_by(self) -> str:
        """The identity two results must share to be pooled together."""

        return f"{self.component_key}:{self.form}" if self.form else self.component_key


def normalize_component_text(value: str) -> str:
    """Normalize a source string to its catalog key form.

    Deliberately minimal — compatibility normalization, case folding, and
    whitespace collapse only. Stripping punctuation or reordering words would
    make two different substances collide, which is the failure this module
    exists to prevent.
    """

    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(folded.split())


#: Exact normalized source strings that name one component form. Every entry is
#: a single determinate substance. Anything not listed here does not resolve.
COMPONENT_FORMS: dict[str, ComponentForm] = {
    # --- vitamin D ---
    "cholecalciferol (vitamin d3)": ComponentForm("vitamin_d", "cholecalciferol", "维生素 D3"),
    "calcidiol (25(oh)d3)": ComponentForm("vitamin_d", "calcidiol", "25-羟维生素 D3"),
    # Bare "Vitamin D" names one nutrient, so it is catalogued even though one of
    # its rows records an iron-and-vitamin-D fortified milk. Same judgement as
    # `EVOO`: the name denotes one substance, and the dirty row is handled at
    # result level rather than by dissolving the identity for every other study.
    # It pools separately from the named forms, since an unnamed form cannot be
    # assumed to be a named one.
    "vitamin d": ComponentForm("vitamin_d", SINGLE_FORM, "维生素 D"),
    # --- iron, by named salt ---
    "硫酸亚铁": ComponentForm("iron", "ferrous_sulfate", "硫酸亚铁"),
    "麦芽酚铁": ComponentForm("iron", "ferric_maltol", "麦芽酚铁"),
    # --- iron with no form named; the same salt under its own aliases ---
    "ferrous sulfate": ComponentForm("iron", "ferrous_sulfate", "硫酸亚铁"),
    "ferrous sulphate": ComponentForm("iron", "ferrous_sulfate", "硫酸亚铁"),
    "oral ferrous sulphate": ComponentForm("iron", "ferrous_sulfate", "硫酸亚铁"),
    # --- sodium, by named salt ---
    "碳酸氢钠": ComponentForm("sodium_bicarbonate", SINGLE_FORM, "碳酸氢钠"),
    "sodium bicarbonate": ComponentForm("sodium_bicarbonate", SINGLE_FORM, "碳酸氢钠"),
    # --- lipid-relevant oils, by provenance ---
    "olive oil": ComponentForm("olive_oil", SINGLE_FORM, "橄榄油"),
    "evoo": ComponentForm("olive_oil", "extra_virgin", "特级初榨橄榄油"),
    "virgin olive oil": ComponentForm("olive_oil", "virgin", "初榨橄榄油"),
    "refined olive oil": ComponentForm("olive_oil", "refined", "精炼橄榄油"),
    "standard olive oil": ComponentForm("olive_oil", SINGLE_FORM, "橄榄油"),
    "coconut oil": ComponentForm("coconut_oil", SINGLE_FORM, "椰子油"),
    "soybean oil": ComponentForm("soybean_oil", SINGLE_FORM, "大豆油"),
    "brazil nut oil": ComponentForm("brazil_nut_oil", SINGLE_FORM, "巴西坚果油"),
    # --- other single substances ---
    # NOTE: "膳食盐" is absent — its recorded form is a salt-reduction *education*
    # programme with no supplement or substitute salt named, so the name
    # overstates what was administered.
    "大豆异黄酮": ComponentForm("soy_isoflavones", SINGLE_FORM, "大豆异黄酮"),
    # NOTE: "必需氨基酸" and "Fiber supplementation" are deliberately absent.
    # Each names a set of substances, not one substance, so neither can prove
    # that two studies administered the same thing — see the module docstring.
    "特定生物活性胶原蛋白肽(scp)": ComponentForm(
        "collagen_peptide", SINGLE_FORM, "生物活性胶原蛋白肽"
    ),
    "特定角豆液体浓缩物": ComponentForm("carob", SINGLE_FORM, "角豆液体浓缩物"),
    "prunes (prunus domestica)": ComponentForm("prunes", SINGLE_FORM, "西梅"),
    "walnut supplementation": ComponentForm("walnut", SINGLE_FORM, "核桃"),
    "大麦嫩叶(barley green)": ComponentForm("barley_grass", SINGLE_FORM, "大麦嫩叶"),
}


def resolve_component(intervention_name: str) -> ComponentForm | None:
    """Resolve one intervention **name** to a component form, or None.

    Takes the name only — deliberately not `results.ingredient_form`, which is
    free text with no controlled vocabulary (see the module docstring). A caller
    holding a result row should pass `ingredient_name`.

    None is the fail-closed answer and covers every case the catalog does not
    positively name: an unmapped substance, a multi-component product, a dietary
    pattern, a nutrient class, and a non-nutrient intervention such as an
    exercise programme. Callers must treat None as "not poolable", never as
    "unknown, so use it".

    **A non-None answer is necessary but not sufficient for poolability.** This
    resolves a *name*, and a name is not always matched by what a study actually
    administered: one result named `EVOO` records a Mediterranean-diet context,
    and no name-only resolver can separate it from the olive-oil results that
    share the name. The final decision is therefore taken one level down, per
    result, by the synthesis-eligibility predicate ADR 0007 requires — using the
    `ingredient_form` text that this function deliberately does not read (see
    the module docstring). Until that predicate exists, this function's answer
    is an input to the decision, not the decision.
    """

    if not intervention_name or not intervention_name.strip():
        return None
    return COMPONENT_FORMS.get(normalize_component_text(intervention_name))


def is_poolable(intervention_name: str) -> bool:
    """Whether one intervention string may enter component-level synthesis."""

    return resolve_component(intervention_name) is not None


def pool_token(intervention_name: str) -> str:
    """The identity a result carries into synthesis, or "" when it has none.

    Stored on the result so the grouping decision is made once, when the
    result is written, and reads back as a plain column — the same way the
    existing scope keys are derived once and stored. Empty means "not
    poolable", and that is what keeps a result out of every component pool
    while leaving it in the evidence body.
    """

    form = resolve_component(intervention_name)
    return form.pooled_by if form else ""


def pooled_by_label(token: str) -> str:
    """The display name for a pooling identity, or "" if it is not catalogued.

    A body that pools one component must name that component in what a patient
    reads. Taking the label from the locked topic instead would describe a
    coconut-oil pool as "dietary oils and solid fats" — the topic's class
    phrase — which is exactly the over-claim this axis removes.
    """

    for form in COMPONENT_FORMS.values():
        if form.pooled_by == token:
            return form.label
    return ""


def demo() -> None:
    """Smallest runnable check for the logic that must not silently widen."""

    # A named component resolves, and its form is part of the identity.
    assert resolve_component("Virgin olive oil").pooled_by == "olive_oil:virgin"
    assert resolve_component("olive oil").pooled_by == "olive_oil"
    assert (
        resolve_component("Virgin olive oil").pooled_by
        != resolve_component("Refined olive oil").pooled_by
    )
    # Normalization is case- and width-insensitive, but not fuzzy.
    assert resolve_component("  OLIVE OIL ").component_key == "olive_oil"
    assert resolve_component("olive oils") is None, "a near miss must not resolve"
    # Multi-component products, dietary patterns and non-nutrients do not pool.
    for text in (
        "钙和维生素D",
        "Fresubin Powder (also known as Fresubin Powder Fibre)",
        "Mediterranean diet",
        "抗阻运动",
        "运动干预",
        "Salt reduction interventions",
        # A class is not a component: several substances under one name.
        "必需氨基酸",
        "Fiber supplementation",
        # Names that do not denote one substance.
        "膳食盐",
        "",
    ):
        assert resolve_component(text) is None, text
    # The pairing the ADR exists to forbid: calcium and vitamin D cannot pool.
    assert not is_poolable("钙和维生素D")
    print(f"component_forms={len(COMPONENT_FORMS)} ok")


if __name__ == "__main__":
    demo()
