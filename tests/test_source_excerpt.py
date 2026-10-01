"""External behavior of the shared verbatim-excerpt grounding rule."""

from __future__ import annotations

import pytest

from genesis_evidence.core.source_excerpt import (
    Excerpt,
    attest,
    attest_segments,
    normalized_text,
    segments_from_document,
)


def _excerpt(text: str) -> Excerpt:
    return Excerpt(field="claims[0].evidence", text=text)


def test_normalization_collapses_width_case_and_whitespace() -> None:
    assert normalized_text("  Lower　25(OH)D   WAS\tassociated ") == (
        "lower 25(oh)d was associated"
    )


def test_excerpt_found_across_a_document_leaf() -> None:
    document = {"abstract": "Measured serum.", "sections": [{"text": "Frailty prevalence rose."}]}
    quote = "frailty prevalence rose"

    assert attest(document, [_excerpt(quote)]).ok


def test_absent_excerpt_is_reported_with_its_field_and_text() -> None:
    result = attest({"abstract": "Nothing here."}, [_excerpt("a sentence that is not present")])

    assert not result.ok
    assert [(item.field, item.text) for item in result.missing] == [
        ("claims[0].evidence", "a sentence that is not present")
    ]


@pytest.mark.parametrize(
    "marker",
    ["(Figure 2)", "(Table 3)", "(Supplementary Table 1)", "(appendix)"],
)
def test_inline_citation_marker_attests_only_in_citations_mode(marker: str) -> None:
    document = {"text": f"Lower 25(OH)D was associated with higher frailty prevalence {marker}."}
    quote = _excerpt("Lower 25(OH)D was associated with higher frailty prevalence.")

    assert attest(document, [quote], tolerance="citations").ok
    assert not attest(document, [quote], tolerance="strict").ok


def test_strict_mode_is_the_default() -> None:
    document = {"text": "Estimate reported (Figure 1)."}

    assert not attest(document, [_excerpt("Estimate reported.")]).ok


def test_all_or_nothing_is_the_callers_decision_over_one_excerpt_at_a_time() -> None:
    segments = segments_from_document({"text": "alpha beta gamma"})

    result = attest_segments(segments, [_excerpt("alpha"), _excerpt("delta")])

    assert [item.text for item in result.attested] == ["alpha"]
    assert [item.text for item in result.missing] == ["delta"]
    assert not result.ok
