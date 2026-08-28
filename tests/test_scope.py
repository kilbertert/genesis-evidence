"""Direct tests for the EvidenceProfileScopeResolver facade.

These mirror the profile-scope assertions in tests/test_review_workflow.py but
exercise the resolver interface directly instead of the store's private
imports, proving the scope rules live behind the resolver seam.
"""

from __future__ import annotations

from genesis_evidence.review.scope import EvidenceProfileScopeResolver


def test_picots_matches_bilingual_age_scope() -> None:
    resolver = EvidenceProfileScopeResolver()
    assert resolver.picots_matches("Adults aged 18 and older", "成年人")
    assert not resolver.picots_matches("Adults aged 60 and older", "年龄 > 50 岁")


def test_picots_matches_duration_units() -> None:
    resolver = EvidenceProfileScopeResolver()
    assert resolver.picots_matches("At least 3 weeks", "After 30 days of intervention")
    assert not resolver.picots_matches("At least 3 weeks", "After 1 week of intervention")
    assert resolver.picots_matches("4 weeks or longer", "三个月")


def test_metric_outcome_matches_creatinine_exclusions() -> None:
    resolver = EvidenceProfileScopeResolver()
    assert resolver.metric_outcome_matches_text("creatinine", "Serum creatinine (sCr)")
    assert not resolver.metric_outcome_matches_text("creatinine", "Creatinine clearance")
    assert not resolver.metric_outcome_matches_text("creatinine", "Urine calcium/creatinine ratio")


def test_profile_scopes_condition_scope_for_metricless_condition() -> None:
    resolver = EvidenceProfileScopeResolver()
    picots = {
        "population": "Adults aged 40 and older",
        "intervention_or_exposure": "Dietary fiber or probiotic intervention",
        "comparator": "Usual care or no intervention",
        "outcomes": "Stool frequency, stool consistency, or constipation symptoms",
        "timing": "At least 4 weeks",
    }
    dimensions = {
        "population": "Adults aged 50 and older with chronic constipation",
        "ingredient_name": "Dietary fiber",
        "outcome": "Stool frequency and constipation symptoms",
        "timepoint": "After 8 weeks",
    }

    assert resolver.profile_scopes(picots, "COND_CHRONIC_CONSTIPATION", dimensions) == {
        "condition:COND_CHRONIC_CONSTIPATION": "慢性便秘"
    }


def test_profile_scopes_metric_for_prediabetes() -> None:
    resolver = EvidenceProfileScopeResolver()
    picots = {
        "population": "Adults aged 18 and older with prediabetes",
        "intervention_or_exposure": (
            "Dietary or lifestyle intervention with a defined nutrition component"
        ),
        "comparator": (
            "Usual care, minimal intervention, or an alternative dietary intervention"
        ),
        "outcomes": "Fasting glucose and HbA1c",
        "timing": "At least 12 weeks",
    }
    dimensions = {
        "population": "Adults aged 30 with prediabetes",
        "ingredient_name": "low GI dietary pattern",
        "ingredient_form": "",
        "dose": "",
        "comparator": "usual care",
        "outcome": "fasting glucose",
        "timepoint": "12 weeks",
    }

    scopes = resolver.profile_scopes(picots, "COND_PREDIABETES", dimensions)
    assert "metric:fasting_glucose" in scopes


def test_resolve_profile_scope_for_metricless_condition() -> None:
    resolver = EvidenceProfileScopeResolver()
    picots = {
        "population": "Adults aged 40 and older",
        "intervention_or_exposure": "Dietary fiber or probiotic intervention",
        "comparator": "Usual care or no intervention",
        "outcomes": "Stool frequency, stool consistency, or constipation symptoms",
        "timing": "At least 4 weeks",
    }
    dimensions = {
        "population": "Adults aged 50 and older with chronic constipation",
        "ingredient_name": "Dietary fiber",
        "outcome": "Stool frequency and constipation symptoms",
        "timepoint": "After 8 weeks",
    }

    key, label = resolver.resolve_profile_scope(
        picots, "COND_CHRONIC_CONSTIPATION", [dimensions],
        requested="", estimate_target="stool frequency",
    )
    assert key == "condition:COND_CHRONIC_CONSTIPATION"
    assert label == "慢性便秘"


def test_resolve_profile_scope_rejects_inconsistent_requested() -> None:
    resolver = EvidenceProfileScopeResolver()
    picots = {
        "population": "Adults aged 40 and older",
        "intervention_or_exposure": "Dietary fiber or probiotic intervention",
        "comparator": "Usual care or no intervention",
        "outcomes": "Stool frequency, stool consistency, or constipation symptoms",
        "timing": "At least 4 weeks",
    }
    dimensions = {
        "population": "Adults aged 50 and older with chronic constipation",
        "ingredient_name": "Dietary fiber",
        "outcome": "Stool frequency and constipation symptoms",
        "timepoint": "After 8 weeks",
    }

    try:
        resolver.resolve_profile_scope(
            picots, "COND_CHRONIC_CONSTIPATION", [dimensions],
            requested="metric:creatinine", estimate_target="stool frequency",
        )
        raise AssertionError("expected ValueError for inconsistent requested scope")
    except ValueError:
        pass


def test_population_text_join() -> None:
    resolver = EvidenceProfileScopeResolver()
    assert resolver.population_text({"population": ["Adults", "60+"]}) == "Adults 60+"
    assert resolver.population_text({"population": []}) == ""
    assert resolver.population_text(None) == ""
    assert resolver.population_text({"population": "not a list"}) == ""