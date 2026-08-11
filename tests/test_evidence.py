from __future__ import annotations

from genesis_evidence.literature.evidence import HeuristicPicoEvidenceExtractor


def test_heuristic_extraction_only_creates_review_candidates() -> None:
    document = {
        "abstract": (
            "This randomized controlled trial enrolled 120 older adults with sarcopenia. "
            "Participants received protein supplementation or placebo."
        ),
        "sections": [
            {
                "title": "Methods",
                "text": (
                    "Participants received 25 g oral whey protein twice per day "
                    "for 12 weeks. Eligible adults had low baseline protein intake."
                ),
            },
            {
                "title": "Results",
                "text": "Protein supplementation significantly improved muscle strength.",
            },
            {
                "title": "Conclusion",
                "text": "There was no significant difference in mortality between groups.",
            },
        ],
    }

    result = HeuristicPicoEvidenceExtractor().extract(document)

    assert result.requires_human_review is True
    assert result.pico.study_design == "randomized_controlled_trial"
    assert result.pico.population
    assert result.pico.intervention
    assert result.pico.comparator
    assert {claim.direction for claim in result.claims} == {"positive", "no_effect"}
    assert all(claim.review_status == "pending_human_review" for claim in result.claims)
    assert all(claim.confidence <= 0.35 for claim in result.claims)
    assert result.extractor_version == "2"
    detail = result.intervention_details[0]
    assert detail.intervention_name == "Protein"
    assert detail.dose_value == 25
    assert detail.dose_unit == "g"
    assert detail.frequency == "twice per day"
    assert detail.duration == "for 12 weeks"
    assert detail.route == "oral"
    assert detail.review_status == "pending_human_review"
