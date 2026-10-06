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
    # --- vitamin D, by named form ---
    "cholecalciferol (vitamin d3)": ComponentForm("vitamin_d", "cholecalciferol", "维生素 D3"),
    "calcidiol (25(oh)d3)": ComponentForm("vitamin_d", "calcidiol", "25-羟维生素 D3"),
    # --- vitamin D with no form named: still one substance ---
    "vitamin d": ComponentForm("vitamin_d", SINGLE_FORM, "维生素 D"),
    # --- iron, by named salt ---
    "硫酸亚铁": ComponentForm("iron", "ferrous_sulfate", "硫酸亚铁"),
    "麦芽酚铁": ComponentForm("iron", "ferric_maltol", "麦芽酚铁"),
    # --- iron with no form named ---
    "ferrous sulfate": ComponentForm("iron", "ferrous_sulfate", "硫酸亚铁"),
    # --- sodium, by named salt ---
    "碳酸氢钠": ComponentForm("sodium_bicarbonate", SINGLE_FORM, "碳酸氢钠"),
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
    "膳食盐": ComponentForm("sodium_chloride", SINGLE_FORM, "膳食盐"),
    "大豆异黄酮": ComponentForm("soy_isoflavones", SINGLE_FORM, "大豆异黄酮"),
    "必需氨基酸": ComponentForm("essential_amino_acids", SINGLE_FORM, "必需氨基酸"),
    "特定生物活性胶原蛋白肽(scp)": ComponentForm(
        "collagen_peptide", SINGLE_FORM, "生物活性胶原蛋白肽"
    ),
    "特定角豆液体浓缩物": ComponentForm("carob", SINGLE_FORM, "角豆液体浓缩物"),
    "prunes (prunus domestica)": ComponentForm("prunes", SINGLE_FORM, "西梅"),
    "walnut supplementation": ComponentForm("walnut", SINGLE_FORM, "核桃"),
    "大麦嫩叶(barley green)": ComponentForm("barley_grass", SINGLE_FORM, "大麦嫩叶"),
    # --- a nutrient class named as a class, still one axis ---
    "fiber supplementation": ComponentForm("dietary_fiber", SINGLE_FORM, "膳食纤维"),
}


def resolve_component(source_text: str) -> ComponentForm | None:
    """Resolve one extracted intervention string to a component form, or None.

    None is the fail-closed answer and covers every case the catalog does not
    positively name: an unmapped substance, a multi-component product, a dietary
    pattern, and a non-nutrient intervention such as an exercise programme.
    Callers must treat None as "not poolable", never as "unknown, so use it".
    """

    if not source_text or not source_text.strip():
        return None
    return COMPONENT_FORMS.get(normalize_component_text(source_text))


def is_poolable(source_text: str) -> bool:
    """Whether one intervention string may enter component-level synthesis."""

    return resolve_component(source_text) is not None


def demo() -> None:
    """Smallest runnable check for the logic that must not silently widen."""

    # A named component resolves, and its form is part of the identity.
    assert resolve_component("EVOO").pooled_by == "olive_oil:extra_virgin"
    assert resolve_component("olive oil").pooled_by == "olive_oil"
    assert resolve_component("EVOO").pooled_by != resolve_component("olive oil").pooled_by
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
        "",
    ):
        assert resolve_component(text) is None, text
    # The pairing the ADR exists to forbid: calcium and vitamin D cannot pool.
    assert not is_poolable("钙和维生素D")
    print(f"component_forms={len(COMPONENT_FORMS)} ok")


if __name__ == "__main__":
    demo()
