"""The catalog-expansion topic plans are reviewable and internally consistent.

These are the questions the next search batch will be run under, so the failures
worth catching are the ones a reviewer would catch by reading: a plan whose PICOTS
would be rejected by `create_topic`, a plan for a condition no report can reach,
and a query that silently drifts from the corpus-wide date/publication-type tail.
"""

from __future__ import annotations

import pytest

from genesis_evidence.core.conditions import CONDITION_BY_CODE
from genesis_evidence.core.store.papers import PICOTS_FIELDS, SEARCH_STREAMS
from genesis_evidence.literature.topic_plans import (
    PUB_TYPE_CLAUSE,
    TOPIC_PLANS,
    TopicPlan,
    full_query,
    plans_for,
)

CUTOFF = "2026-10-09"


def test_every_plan_names_a_catalog_condition() -> None:
    unknown = [p.condition_code for p in TOPIC_PLANS if p.condition_code not in CONDITION_BY_CODE]

    assert not unknown, f"plans for unknown conditions: {unknown}"


def test_every_planned_condition_is_reachable_from_a_report() -> None:
    """A plan for an unreachable condition would collect literature no patient sees.

    Reach is `metrics`, not intent: a condition with an empty metric tuple receives
    a report only through the questionnaire or population-attribute path, neither of
    which exists yet (ADR 0008). Collecting for it now pays the screening cost and
    cannot deliver the card.
    """

    unreachable = [
        p.condition_code for p in TOPIC_PLANS if not CONDITION_BY_CODE[p.condition_code].metrics
    ]

    assert not unreachable, f"plans for conditions with no report reach: {unreachable}"


@pytest.mark.parametrize("plan", TOPIC_PLANS, ids=lambda p: p.condition_code)
def test_plan_passes_the_topic_creation_contract(plan: TopicPlan) -> None:
    """Everything `create_topic` validates, checked without a database.

    A plan that would be refused at write time is a plan nobody can act on, and the
    refusal would arrive on the host, mid-batch, after earlier topics were created.
    """

    assert set(plan.picots) == PICOTS_FIELDS
    assert all(value.strip() for value in plan.picots.values())
    assert plan.review_question.strip()
    assert plan.eligible_study_designs and all(d.strip() for d in plan.eligible_study_designs)
    assert plan.inclusion_criteria and all(c.strip() for c in plan.inclusion_criteria)
    assert len(set(plan.exclusion_reasons)) == len(plan.exclusion_reasons)
    assert plan.exclusion_reasons
    assert set(plan.required_search_streams) <= SEARCH_STREAMS
    assert plan.required_search_streams


def test_one_plan_per_condition_and_per_topic_code() -> None:
    conditions = [p.condition_code for p in TOPIC_PLANS]
    codes = [p.code for p in TOPIC_PLANS]

    assert len(set(conditions)) == len(conditions), "two plans for one condition"
    assert len(set(codes)) == len(codes), "two plans with one topic code"


@pytest.mark.parametrize("plan", TOPIC_PLANS, ids=lambda p: p.condition_code)
def test_query_carries_the_shared_tail_and_owns_its_terms(plan: TopicPlan) -> None:
    """The date bound and publication types come from one place, not each plan."""

    query = full_query(plan, cutoff=CUTOFF)

    assert PUB_TYPE_CLAUSE in query
    assert f"FIRST_PDATE:[1900-01-01 TO {CUTOFF}]" in query
    assert plan.query_core in query
    # The tail must not be duplicated inside the plan's own terms.
    assert "FIRST_PDATE" not in plan.query_core
    assert "PUB_TYPE" not in plan.query_core


def test_plans_for_returns_catalog_order_and_refuses_an_unknown_condition() -> None:
    plans = plans_for(["COND_METABOLIC_SYNDROME", "COND_OVERWEIGHT_OBESITY"])

    assert [p.condition_code for p in plans] == [
        "COND_METABOLIC_SYNDROME",
        "COND_OVERWEIGHT_OBESITY",
    ]
    with pytest.raises(ValueError, match="no topic plan"):
        plans_for(["COND_NOT_IN_THE_CATALOG"])
