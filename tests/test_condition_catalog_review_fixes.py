"""T2 review fixes: no cascade to patient findings, no cross-sex leakage.

Both are correctness properties Devin Review raised against the catalog expansion;
each is asserted at the seam that actually implements it.
"""

from __future__ import annotations

from genesis_evidence.core.conditions import CONDITION_BY_CODE
from genesis_evidence.core.metrics import METRIC_LABELS
from genesis_evidence.review.scope import EvidenceProfileScopeResolver

RESOLVER = EvidenceProfileScopeResolver()

#: Sex/population-restricted conditions. The matcher has no sex context, so these
#: must stay unreachable by metric until the population-attribute path (T7) exists.
SEX_RESTRICTED = ("COND_MALE_OSTEOPOROSIS", "COND_BPH_RISK", "COND_MENOPAUSE_HEALTH_RISK")


def test_sex_restricted_conditions_claim_no_metric() -> None:
    """A woman's low bone density must not be told she has 「男性骨质疏松风险」.

    `EvidenceMatcher` iterates `CONDITIONS_BY_METRIC` and receives no sex context,
    so a metric claimed by a male-only or female-only condition would reach every
    patient. Until T7 supplies the population gate, these claim nothing.
    """

    offenders = {
        code: CONDITION_BY_CODE[code].metrics
        for code in SEX_RESTRICTED
        if CONDITION_BY_CODE[code].metrics
    }

    assert not offenders, (
        "sex-restricted conditions must not claim metrics until the population "
        f"attribute reach path exists: {offenders}"
    )


def test_a_metric_in_the_catalog_can_always_be_assigned_a_metric_scope() -> None:
    """Every canonical metric is scope-assignable — the cross-module invariant.

    A metric the resolver cannot match is never given a ``metric:<code>`` scope,
    and a card whose scope is not ``metric:<code>`` is never served to a patient.
    The resolver's synonym registry is hand-maintained, so this drift is silent:
    the card simply never appears. Asserted over the whole catalog, not a sample.
    """

    unassignable = []
    for metric_code in METRIC_LABELS:
        label = METRIC_LABELS[metric_code]
        # A topic and a result that name the metric by its own label must be
        # recognizable as being about that metric.
        if not RESOLVER.metric_outcome_matches_text(metric_code, label):
            unassignable.append(metric_code)

    assert not unassignable, f"metrics that can never be scoped: {unassignable}"


def test_second_batch_metrics_can_earn_a_metric_scope() -> None:
    """The end-to-end shape of finding 1: a topic about a new metric gets a scope.

    Without this, a card for an electrolyte or CBC topic could be reviewed and
    published and still never reach a patient, because the resolver only emitted
    ``outcome:`` scopes for metrics it had synonyms for.
    """

    def scopes(condition_code: str, outcome: str) -> dict[str, str]:
        picots = {
            "population": "Adults with hypertension",
            "intervention_or_exposure": "sodium supplementation",
            "outcomes": outcome,
            "timing": "12 weeks",
            "setting": "outpatient",
        }
        dimensions = {
            "population": picots["population"],
            "ingredient_name": "sodium",
            "ingredient_form": "",
            "dose": "1000 mg",
            "outcome": outcome,
            "timepoint": "12 weeks",
        }
        return RESOLVER.profile_scopes(picots, condition_code, dimensions)

    assert scopes("COND_ELECTROLYTE_DISTURBANCE", "Serum sodium") == {"metric:sodium": "钠"}
    assert scopes("COND_INFECTION_INFLAMMATION_PATTERN", "Neutrophils percentage") == {
        "metric:neutrophils_percent": "中性粒细胞百分比"
    }
    assert scopes("COND_URINARY_ABNORMALITY", "Urine protein") == {"metric:urine_protein": "尿蛋白"}


def test_short_derived_codes_do_not_match_inside_unrelated_words() -> None:
    """`psa` must not match "capsaicin"; the Chinese label still matches 血钠."""

    assert not RESOLVER.metric_outcome_matches_text("psa", "Capsaicin intake")
    assert RESOLVER.metric_outcome_matches_text("psa", "PSA")
    # Chinese labels are never substring-risky, so a derived metric still matches.
    assert RESOLVER.metric_outcome_matches_text("sodium", "血钠")
    assert RESOLVER.metric_outcome_matches_text("sodium", "Sodium")
