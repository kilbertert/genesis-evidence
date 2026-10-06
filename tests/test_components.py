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
    evoo = resolve_component("EVOO")
    plain = resolve_component("Olive oil")
    assert evoo is not None and plain is not None
    # A form difference is a real difference: unrefined vs refined is not the
    # same exposure, so these must not silently pool.
    assert evoo.pooled_by != plain.pooled_by


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


def test_measured_unmapped_population_is_reported() -> None:
    """Record the fail-closed population rather than assuming it is small.

    Measured against the 2026-10-06 corpus: 23 of 59 strings resolve, 36 do not.
    The catalog is deliberately narrow — most of the corpus is a multi-component
    supplement, a whole dietary pattern, or a non-nutrient intervention. This
    count is the honest exposure the ADR promised to report; a regression that
    widened resolution would show up here as the resolved count climbing.
    """

    resolved = [text for text in APPROVED_INTERVENTION_CORPUS if is_poolable(text)]
    unresolved = [text for text in APPROVED_INTERVENTION_CORPUS if not is_poolable(text)]
    assert len(resolved) == 23, sorted(unresolved)
    assert len(unresolved) == 36
    assert len(resolved) + len(unresolved) == 59


def test_an_unnamed_form_is_not_guessed_to_be_a_named_one() -> None:
    # "Vitamin D" does not say which form; "Cholecalciferol (vitamin D3)" does.
    # Guessing that the unnamed one is cholecalciferol would be a pool the
    # evidence does not support, so they stay separate identities.
    unnamed = resolve_component("Vitamin D")
    named = resolve_component("Cholecalciferol (vitamin D3)")
    assert unnamed is not None and named is not None
    assert unnamed.component_key == named.component_key
    assert unnamed.pooled_by != named.pooled_by
