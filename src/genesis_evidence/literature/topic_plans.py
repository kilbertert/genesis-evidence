"""Versioned topic plans for the catalog expansion (PRD #239).

A topic is the unit of literature collection: a locked PICOTS question, the study
designs it will accept, and the search streams it must be covered by. Until now
they were created ad hoc, one at a time, and the wording of each was never
reviewable in the repository. This module makes the next batch's questions a
reviewed artifact.

**Scope is the reachable catalog, not the whole catalog.** Only conditions a
report can actually reach get a plan. A condition whose only reach path is a
questionnaire or a patient attribute (ADR 0008) is skipped on purpose: collecting
literature for it would produce cards no patient can be served from, and the cost
— every topic is a screening ledger someone has to close — is paid now for a
benefit that is not available yet. `tests/test_topic_plans.py` enforces that rule
rather than leaving it as an intention.

The wording is a starting point for review, not a settled scientific position.
Editing a plan before its first collection run is cheap; editing it afterwards
means the ledger has to be reconciled, which is why they live here where the
change shows up in a diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The exclusion vocabulary the newest topics already use. Kept in one place so a
#: new plan cannot invent a reason that no reviewer will recognise.
DEFAULT_EXCLUSION_REASONS: tuple[str, ...] = (
    "wrong_population",
    "wrong_intervention_or_exposure",
    "wrong_comparator",
    "wrong_outcome",
    "wrong_timing_or_setting",
    "ineligible_study_design",
    "no_eligible_full_text",
)

#: Designs a nutrition-evidence topic will read. Narrower than `STUDY_DESIGNS`:
#: animal and in-vitro work never reaches a card, so admitting it only enlarges the
#: screening ledger.
DEFAULT_STUDY_DESIGNS: tuple[str, ...] = (
    "systematic_review_meta_analysis",
    "randomized_controlled_trial",
    "cohort_study",
    "cross_sectional_study",
)

DEFAULT_INCLUSION_CRITERIA: tuple[str, ...] = (
    "Adults or mixed adult populations relevant to the locked PICOTS",
    "Nutrition or lifestyle exposure/intervention with the named outcome",
    "Formal publication with retrievable open full text",
)

#: The tail every search shares: the publication types this corpus reads and the
#: date bound. `FIRST_PDATE` is how the existing runs spell the evidence cutoff.
PUB_TYPE_CLAUSE: str = (
    '(PUB_TYPE:"randomized controlled trial"'
    ' OR PUB_TYPE:"controlled clinical trial"'
    ' OR PUB_TYPE:"systematic review"'
    ' OR PUB_TYPE:"meta-analysis")'
)

#: The field both clauses search. One constant so a plan cannot quietly search a
#: narrower field than the rest of the corpus does.
_T = "TITLE_ABS"


def _or(field_name: str, *terms: str) -> str:
    """`FIELD:(a OR b)` — assembled from short pieces, so nothing needs wrapping."""

    return f"{field_name}:(" + " OR ".join(terms) + ")"


def _and(*parts: str) -> str:
    return " AND ".join(parts)


@dataclass(frozen=True, slots=True)
class TopicPlan:
    """One reviewable PICOTS question, ready to become a locked topic."""

    code: str
    condition_code: str
    review_question: str
    #: The Europe PMC term part of the search, without the shared tail. The date
    #: bound and publication types are appended by `full_query` so they live in one
    #: place; everything here is the reviewer's scientific judgement.
    query_core: str
    picots: dict[str, str] = field(default_factory=dict)
    eligible_study_designs: tuple[str, ...] = DEFAULT_STUDY_DESIGNS
    inclusion_criteria: tuple[str, ...] = DEFAULT_INCLUSION_CRITERIA
    exclusion_reasons: tuple[str, ...] = DEFAULT_EXCLUSION_REASONS
    required_search_streams: tuple[str, ...] = ("effect",)


def full_query(plan: TopicPlan, *, cutoff: str) -> str:
    """The complete Europe PMC query: the plan's terms plus the shared tail."""

    return (
        f"({plan.query_core}) AND {PUB_TYPE_CLAUSE} "
        f"AND FIRST_PDATE:[1900-01-01 TO {cutoff}]"
    )


def _picots(
    population: str, intervention: str, comparator: str, outcomes: str, timing: str
) -> dict[str, str]:
    """All six PICOTS fields, with the setting this corpus actually draws from.

    `create_topic` refuses a topic with any empty field, so building the dict in one
    place is what keeps every plan from silently omitting one.
    """

    return {
        "population": population,
        "intervention_or_exposure": intervention,
        "comparator": comparator,
        "outcomes": outcomes,
        "timing": timing,
        "setting": "Community or outpatient",
    }


#: The exposure-side terms most plans share, so that clause reads the same way
#: across topics instead of drifting plan by plan.
_NUTRITION = _or(_T, "diet*", "nutrition", "lifestyle", "intervention")

TOPIC_PLANS: tuple[TopicPlan, ...] = (
    TopicPlan(
        code="nutrition-overweight-obesity",
        condition_code="COND_OVERWEIGHT_OBESITY",
        review_question=(
            "Among adults, what nutrition or lifestyle interventions change body "
            "weight, waist circumference, or body fat in overweight or obesity?"
        ),
        query_core=_and(
            _or(_T, "obesity", "overweight", '"weight loss"', '"waist circumference"'),
            _or(_T, "diet*", "nutrition", "calorie", "lifestyle", "intervention"),
        ),
        picots=_picots(
            "Adults with overweight or obesity",
            "Dietary pattern, calorie restriction, or a defined nutrition intervention",
            "Usual diet, no intervention, or an alternative diet",
            "Body weight, BMI, waist circumference, or body fat percentage",
            "At least 12 weeks",
        ),
    ),
    TopicPlan(
        code="nutrition-metabolic-syndrome",
        condition_code="COND_METABOLIC_SYNDROME",
        review_question=(
            "Among adults, what nutrition or lifestyle interventions affect the "
            "clustered metabolic risk factors of metabolic syndrome?"
        ),
        query_core=_and(
            _or(_T, '"metabolic syndrome"', '"insulin resistance"', '"abdominal obesity"'),
            _NUTRITION,
        ),
        picots=_picots(
            "Adults with metabolic syndrome or its components",
            "Dietary pattern or a defined nutrition/lifestyle intervention",
            "Usual care, no intervention, or an alternative diet",
            "Waist circumference, fasting glucose, triglycerides, HDL-C, or blood pressure",
            "At least 12 weeks",
        ),
    ),
    TopicPlan(
        code="nutrition-liver-enzymes-and-proteins",
        condition_code="COND_LIVER_FUNCTION_PATTERN",
        review_question=(
            "Among adults, what nutrition or lifestyle exposures are associated with "
            "bilirubin, total protein, globulin, or the albumin/globulin ratio?"
        ),
        query_core=_and(
            _or(_T, "bilirubin", "globulin", '"liver function"', '"albumin globulin ratio"'),
            _or(_T, "diet*", "nutrition", "alcohol", "exposure"),
        ),
        picots=_picots(
            "Adults with or without liver disease",
            "Dietary pattern, alcohol intake, or a defined nutrition exposure",
            "Usual diet, no exposure, or an alternative diet",
            "Total, direct, or indirect bilirubin, total protein, globulin, ratio",
            "At least 8 weeks",
        ),
    ),
    TopicPlan(
        code="nutrition-electrolytes",
        condition_code="COND_ELECTROLYTE_DISTURBANCE",
        review_question=(
            "Among adults, what nutrition or lifestyle exposures change serum sodium, "
            "potassium, chloride, phosphate, or calcium?"
        ),
        query_core=_and(
            _or(
                _T,
                "sodium",
                "potassium",
                "chloride",
                "phosphate",
                "calcium",
                "electrolyte*",
            ),
            _or(_T, "diet*", "nutrition", "intake", "intervention"),
        ),
        picots=_picots(
            "Adults, including those with kidney or cardiovascular risk",
            "Dietary pattern, sodium or potassium intake, or a nutrition intervention",
            "Usual diet, no intervention, or an alternative diet",
            "Serum sodium, potassium, chloride, phosphate, or albumin-corrected calcium",
            "At least 4 weeks",
        ),
    ),
    TopicPlan(
        code="nutrition-white-cell-differential",
        condition_code="COND_INFECTION_INFLAMMATION_PATTERN",
        review_question=(
            "Among adults, what nutrition or lifestyle exposures are associated with "
            "white cell count or the leukocyte differential?"
        ),
        query_core=_and(
            _or(
                _T,
                '"white blood cell"',
                "leukocyte*",
                "neutrophil*",
                "lymphocyte*",
                "monocyte*",
                "eosinophil*",
                "basophil*",
            ),
            _or(_T, "diet*", "nutrition", "exposure"),
        ),
        picots=_picots(
            "Adults with or without inflammatory conditions",
            "Dietary pattern or a defined nutrition exposure",
            "Usual diet, no exposure, or an alternative diet",
            "Total white cell count or the leukocyte differential counts",
            "At least 4 weeks",
        ),
    ),
    TopicPlan(
        code="nutrition-urinary-markers",
        condition_code="COND_URINARY_ABNORMALITY",
        review_question=(
            "Among adults, what nutrition or lifestyle exposures are associated with "
            "urinary protein, leukocytes, erythrocytes, specific gravity, or pH?"
        ),
        query_core=_and(
            _or(_T, "urine", "urinary", "proteinuria", "hematuria"),
            _or(_T, "diet*", "nutrition", "fluid", "exposure"),
        ),
        picots=_picots(
            "Adults, including those with urinary or kidney risk",
            "Dietary pattern, fluid intake, or a defined nutrition exposure",
            "Usual diet, no exposure, or an alternative diet",
            "Urine protein, leukocytes, erythrocytes, specific gravity, or pH",
            "At least 4 weeks",
        ),
    ),
    TopicPlan(
        code="nutrition-cardiac-enzymes",
        condition_code="COND_CARDIAC_ENZYME_PATTERN",
        review_question=(
            "Among adults, what nutrition or lifestyle exposures are associated with "
            "creatine kinase, CK-MB, or lactate dehydrogenase?"
        ),
        query_core=_and(
            _or(_T, '"creatine kinase"', '"CK-MB"', '"lactate dehydrogenase"'),
            _or(_T, "diet*", "nutrition", "supplement*", "exercise"),
        ),
        picots=_picots(
            "Adults, including those who exercise or take lipid-lowering therapy",
            "Dietary pattern, supplement use or cessation, or a lifestyle exposure",
            "Usual diet, no exposure, or an alternative diet",
            "Creatine kinase, CK-MB, or lactate dehydrogenase",
            "At least 4 weeks",
        ),
    ),
    TopicPlan(
        code="nutrition-prostate-specific-antigen",
        condition_code="COND_PROSTATE_REVIEW_PROMPT",
        review_question=(
            "Among adult men, what nutrition or lifestyle exposures are associated "
            "with prostate-specific antigen concentration?"
        ),
        query_core=_and(
            _or(_T, '"prostate-specific antigen"', '"prostate specific antigen"'),
            _or(_T, "diet*", "nutrition", "supplement*", "intervention"),
        ),
        picots=_picots(
            "Adult men",
            "Dietary pattern or a defined nutrition intervention",
            "Usual diet, no intervention, or an alternative diet",
            "Serum prostate-specific antigen",
            "At least 8 weeks",
        ),
    ),
    TopicPlan(
        code="nutrition-rheumatoid-factor",
        condition_code="COND_RHEUMATOID_REVIEW_PROMPT",
        review_question=(
            "Among adults, what nutrition or lifestyle exposures are associated with "
            "rheumatoid factor?"
        ),
        query_core=_and(
            _or(_T, '"rheumatoid factor"'),
            _or(_T, "diet*", "nutrition", "exposure"),
        ),
        picots=_picots(
            "Adults with or without joint symptoms",
            "Dietary pattern or a defined nutrition exposure",
            "Usual diet, no exposure, or an alternative diet",
            "Rheumatoid factor",
            "At least 8 weeks",
        ),
    ),
    TopicPlan(
        code="nutrition-vision-and-intraocular-pressure",
        condition_code="COND_EYE_REVIEW_PROMPT",
        review_question=(
            "Among adults, what nutrition or lifestyle exposures are associated with "
            "visual acuity or intraocular pressure?"
        ),
        query_core=_and(
            _or(_T, '"visual acuity"', '"intraocular pressure"', "vision", "glaucoma"),
            _or(_T, "diet*", "nutrition", "supplement*", "intervention"),
        ),
        picots=_picots(
            "Adults, including those with elevated intraocular pressure",
            "Dietary pattern, specific nutrients, or a defined lifestyle exposure",
            "Usual diet, no intervention, or an alternative diet",
            "Uncorrected visual acuity or intraocular pressure",
            "At least 8 weeks",
        ),
    ),
)


PLANS_BY_CONDITION: dict[str, TopicPlan] = {
    plan.condition_code: plan for plan in TOPIC_PLANS
}


def plans_for(condition_codes: list[str]) -> list[TopicPlan]:
    """The plans for the named conditions, in the order asked for.

    Raises rather than skipping when a name has no plan: a caller that asked for a
    condition and silently got nothing would report a smaller batch as success.
    """

    missing = [code for code in condition_codes if code not in PLANS_BY_CONDITION]
    if missing:
        raise ValueError(f"no topic plan for: {', '.join(sorted(missing))}")
    return [PLANS_BY_CONDITION[code] for code in condition_codes]
