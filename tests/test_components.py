"""The component catalog must resolve exactly and fail closed (ADR 0007)."""

from genesis_evidence.core.components import (
    COMPONENT_FORMS,
    is_poolable,
    normalize_component_text,
    resolve_component,
)

# Every one of the 59 distinct intervention strings that feed an approved result
# in the 2026-10-06 development database, transcribed verbatim. This is the real
# corpus, not a sample: the slice's whole job is to be honest about all of it.
APPROVED_INTERVENTION_CORPUS = (
    "高钙鲜奶（含乳酸钙、维生素D、酪蛋白磷酸肽）",
    "钙和维生素D",
    "大麦嫩叶（barley green）",
    "麦芽酚铁",
    "CalGo®",
    "Coconut oil",
    "CalGo® salmon bone complex",
    "中链甘油三酯油和黄油",
    "Fiber supplementation",
    "高钙鲜奶",
    "Olive oil",
    "Prunes (Prunus domestica)",
    "EVOO",
    "Low-carbohydrate, high-protein calorie-restricted diet with nutrition bars",
    "High-protein diet",
    "Fresubin Powder (also known as Fresubin Powder Fibre)",
    "硫酸亚铁",
    "特定生物活性胶原蛋白肽（SCP）",
    "低钠高钾盐替代品",
    "中等蛋白、中等升糖指数饮食",
    "Vitamin D",
    "NEO-MUNE oral nutritional supplement",
    "Mediterranean diet",
    "膳食盐",
    "电解碱性水",
    "特定角豆液体浓缩物",
    "抗阻运动",
    "Standard Olive Oil",
    "Soybean oil",
    "Self-help materials",
    "Salt reduction interventions",
    "Entrasol Platinum oral nutritional supplement",
    "Eggshell-derived calcium and vitamin D fortified HMR combined with habitual exercise",
    "Eggshell-derived calcium and vitamin D",
    "Butter, coconut oil, olive oil",
    "运动干预；联合组另含植物低聚肽、酪蛋白肽、支链氨基酸、CaHMB",
    "运动干预；植物低聚肽、酪蛋白肽、支链氨基酸、CaHMB；个体化营养教育",
    "运动干预",
    "碳酸氢钠",
    "植物低聚肽、酪蛋白肽、支链氨基酸、CaHMB",
    "必需氨基酸",
    "大豆蛋白和大豆异黄酮",
    "大豆异黄酮",
    "Walnut supplementation",
    "Virgin olive oil",
    "sodium bicarbonate (SB) 和 potassium citrate/sodium citrate (PCSC)",
    # fmt: this string is transcribed verbatim, including its length
    "Safflower, sunflower, rapeseed, flaxseed, corn, olive, soybean, palm,"
    " coconut oil, and beef fat",
    "Refined olive oil blend",
    "Refined olive oil",
    "Olive oil enriched with n-3 PUFA (EPA/DHA)",
    "Mediterranean diet variants",
    "Low-salt foods",
    "Low-carbohydrate diet",
    "High-calcium milk",
    "Diet-only arm (lifestyle therapy without exercise)",
    "Cholecalciferol (vitamin D3)",
    "Calorie restriction diet",
    "Calcidiol (25(OH)D3)",
    "Brazil nut oil",
)


def test_corpus_is_the_full_measured_set() -> None:
    assert len(set(APPROVED_INTERVENTION_CORPUS)) == 59


def test_every_catalog_entry_is_normalized_and_individually_named() -> None:
    for source, form in COMPONENT_FORMS.items():
        assert source == normalize_component_text(source), source
        assert form.component_key and form.label


def test_resolution_is_exact_and_fails_closed() -> None:
    assert resolve_component("Olive oil") is not None
    assert resolve_component(" OLIVE   OIL ") is not None
    # Near misses must not resolve — this is the property the ADR buys.
    for near_miss in ("olive oil blend", "olive oils", "extra olive oil", "oil"):
        assert resolve_component(near_miss) is None, near_miss


def test_forms_keep_distinct_pooling_identities() -> None:
    virgin = resolve_component("Virgin olive oil")
    refined = resolve_component("Refined olive oil")
    assert virgin is not None and refined is not None
    # A form difference is a real difference: unrefined vs refined is not the
    # same exposure, so these must not silently pool even though both are
    # olive oil.
    assert virgin.component_key == refined.component_key == "olive_oil"
    assert virgin.pooled_by != refined.pooled_by


def test_forms_of_one_component_still_share_a_component() -> None:
    # The pair that must UNIFY: four spellings of olive oil are one axis.
    keys = {
        resolve_component(text).component_key
        for text in ("Olive oil", "Standard Olive Oil", "Virgin olive oil", "Refined olive oil")
    }
    assert keys == {"olive_oil"}


def test_the_compound_interventions_the_adr_forbids_do_not_pool() -> None:
    # These are the exact strings behind the published cards that pool ten
    # different interventions under one certainty value.
    for compound in (
        "钙和维生素D",
        "Eggshell-derived calcium and vitamin D",
        "高钙鲜奶（含乳酸钙、维生素D、酪蛋白磷酸肽）",
        "CalGo®",
        "Butter, coconut oil, olive oil",
        "Safflower, sunflower, rapeseed, flaxseed, corn, olive, soybean, palm,"
        " coconut oil, and beef fat",
    ):
        assert not is_poolable(compound), compound


def test_non_nutrient_interventions_never_resolve() -> None:
    # Exercise programmes reached the pipeline through a loose screening gate.
    # They must never be poolable as if they were a nutrient.
    for non_nutrient in (
        "抗阻运动",
        "运动干预",
        "Self-help materials",
        "Diet-only arm (lifestyle therapy without exercise)",
    ):
        assert not is_poolable(non_nutrient), non_nutrient


#: Every corpus string that resolves, and the pooling identity it must get.
#: Recorded per entry, not as a total: a total is satisfied by one string
#: starting to resolve while another stops, which is exactly the silent swap
#: that would move evidence between pools.
EXPECTED_POOLING_IDENTITIES = {
    "大麦嫩叶（barley green）": "barley_grass",
    "麦芽酚铁": "iron:ferric_maltol",
    "Coconut oil": "coconut_oil",
    "EVOO": "olive_oil:extra_virgin",
    "Olive oil": "olive_oil",
    "Prunes (Prunus domestica)": "prunes",
    "硫酸亚铁": "iron:ferrous_sulfate",
    "特定生物活性胶原蛋白肽（SCP）": "collagen_peptide",
    "特定角豆液体浓缩物": "carob",
    "Standard Olive Oil": "olive_oil",
    "Soybean oil": "soybean_oil",
    "碳酸氢钠": "sodium_bicarbonate",
    "大豆异黄酮": "soy_isoflavones",
    "Walnut supplementation": "walnut",
    "Virgin olive oil": "olive_oil:virgin",
    "Refined olive oil": "olive_oil:refined",
    "Cholecalciferol (vitamin D3)": "vitamin_d:cholecalciferol",
    "Calcidiol (25(OH)D3)": "vitamin_d:calcidiol",
    "Brazil nut oil": "brazil_nut_oil",
}

#: Strings that must NOT resolve, and the reason they must not. Pinning the
#: reason keeps a future edit from "fixing" one of these by adding it.
EXPECTED_UNRESOLVED_REASONS = {
    "钙和维生素D": "multi-component supplement",
    "Vitamin D": "name spans a supplement and a fortified milk",
    "膳食盐": "name overstates a salt-reduction education programme",
    "Fiber supplementation": "class, not a substance",
    "必需氨基酸": "class, not a substance",
    "抗阻运动": "not a nutrient",
    "运动干预": "not a nutrient",
}


def test_each_entry_resolves_to_its_recorded_pooling_identity() -> None:
    for text, expected in EXPECTED_POOLING_IDENTITIES.items():
        form = resolve_component(text)
        assert form is not None, f"{text} stopped resolving"
        assert form.pooled_by == expected, f"{text} -> {form.pooled_by} != {expected}"


def test_the_corpus_partition_is_pinned_both_ways() -> None:
    """Both halves are checked, so a swap cannot hide in the totals."""

    for text, reason in EXPECTED_UNRESOLVED_REASONS.items():
        assert not is_poolable(text), f"{text} resolved but is {reason}"

    resolved = {text for text in APPROVED_INTERVENTION_CORPUS if is_poolable(text)}
    assert resolved == set(EXPECTED_POOLING_IDENTITIES), (
        f"unexpected: {sorted(resolved ^ set(EXPECTED_POOLING_IDENTITIES))}"
    )


def test_a_class_name_is_not_poolable() -> None:
    """A name covering several substances cannot prove two studies matched."""

    assert not is_poolable("Fiber supplementation")
    assert not is_poolable("必需氨基酸")


def test_named_forms_of_one_vitamin_are_separate_identities() -> None:
    # D3 and calcidiol are the same component in different forms; a form
    # difference is a real difference and they must not silently pool.
    d3 = resolve_component("Cholecalciferol (vitamin D3)")
    calcidiol = resolve_component("Calcidiol (25(OH)D3)")
    assert d3 is not None and calcidiol is not None
    assert d3.component_key == calcidiol.component_key == "vitamin_d"
    assert d3.pooled_by != calcidiol.pooled_by


def test_a_name_too_coarse_to_be_a_component_fails_closed() -> None:
    """Names whose own evidence showed the name does not denote one substance.

    Both were removed from the catalog after reading the `ingredient_form` text
    of the rows sharing that name — the only use that column has here. The test
    is whether the *name* denotes one substance, not whether every row is clean:
    `EVOO` stays (it names extra-virgin olive oil; one row used it loosely),
    while these two do not.
    """

    for too_coarse, why in (
        ("Vitamin D", "names a family, not one substance"),
        ("膳食盐", "names a salt-reduction education programme"),
    ):
        assert resolve_component(too_coarse) is None, f"{too_coarse} {why}"
    # The contrast that keeps this judgement honest: a name that IS one
    # substance stays, even though a row of its evidence is imprecise.
    assert resolve_component("EVOO") is not None


def test_name_resolution_is_necessary_but_not_sufficient_for_poolability() -> None:
    """The limit that no name-only resolver can cross.

    `EVOO` resolves to extra-virgin olive oil. One recorded result carrying that
    name is a Mediterranean-diet trial, and only the olive-oil results belong in
    that pool. This function cannot tell them apart, so its answer may not be
    used as the final poolability decision: per-result synthesis eligibility —
    which can read `ingredient_form` — has to reject the diet trial. Pinned so
    that a slice does not treat a resolved name as a green light.
    """

    assert resolve_component("EVOO") is not None
    # Both results share the name, so nothing here separates them.
    assert resolve_component("EVOO") == resolve_component("EVOO")
