"""The rebuild report must not file one problem under another (#227).

Both functions below were rewritten after review found them wrong, and neither
had a test. What they decide is what an operator reads before running an
irreversible migration, so the shapes that were wrong are pinned here.
"""

from __future__ import annotations

from scripts.rebuild_evidence_bodies import _outcome_kind, classify_scope_anomalies


def test_a_scope_with_one_card_is_not_an_anomaly() -> None:
    several, duplicates = classify_scope_anomalies({("C", "metric:x"): [("card-1", "a")]})

    assert several == []
    assert duplicates == []


def test_several_components_at_one_scope_are_reported() -> None:
    several, duplicates = classify_scope_anomalies(
        {("C", "metric:x"): [("card-1", "coconut_oil"), ("card-2", "soybean_oil")]}
    )

    assert several == [["C", "metric:x"]]
    assert duplicates == []


def test_duplicate_cards_for_one_component_are_reported_separately() -> None:
    several, duplicates = classify_scope_anomalies(
        {("C", "metric:x"): [("card-1", "coconut_oil"), ("card-2", "coconut_oil")]}
    )

    assert several == []
    assert duplicates == [["C", "metric:x"]]


def test_a_mixed_scope_is_reported_under_both_headings() -> None:
    """The case an exclusive classification hid.

    Two cards for one component plus a card for another is both a presentation
    question and a duplicate publication. Filing it only as "several components"
    would present the duplicate as expected, which is exactly the defect the two
    conditions were split to avoid.
    """

    several, duplicates = classify_scope_anomalies(
        {
            ("C", "metric:x"): [
                ("card-1", "coconut_oil"),
                ("card-2", "coconut_oil"),
                ("card-3", "soybean_oil"),
            ]
        }
    )

    assert several == [["C", "metric:x"]]
    assert duplicates == [["C", "metric:x"]]


def test_a_paper_that_published_every_card_rebuilt_completely() -> None:
    kind, _ = _outcome_kind({"cards": [{"status": "published"}, {"status": "published"}]})

    assert kind == "complete"


def test_a_paper_with_some_cards_published_rebuilt_partially() -> None:
    """"Not every card published" is a state, not a failure.

    A paper covering several topics legitimately has topics with no matching
    PICOTS scope. Counting that as a failure reported 21 of 39 papers as failed
    on a run that had not failed at all.
    """

    kind, why = _outcome_kind(
        {
            "cards": [
                {"status": "published"},
                {"status": "no_matching_picots_result_scope"},
            ]
        }
    )

    assert kind == "partial"
    assert "1/2" in why


def test_a_paper_with_no_published_card_did_not_rebuild() -> None:
    kind, why = _outcome_kind({"cards": [{"status": "waiting_for_complete_evidence_body"}]})

    assert kind == "none"
    assert "no card published" in why


def test_attention_required_is_not_a_complete_rebuild() -> None:
    """`auto_review_paper` returns normally when it could not finish."""

    kind, why = _outcome_kind({"status": "attention_required", "reason": "ledger open"})

    assert kind == "none"
    assert "attention_required" in why
    assert "ledger open" in why


def test_no_card_at_all_did_not_rebuild() -> None:
    assert _outcome_kind({"cards": []})[0] == "none"
    assert _outcome_kind({})[0] == "none"
    assert _outcome_kind(None)[0] == "none"
