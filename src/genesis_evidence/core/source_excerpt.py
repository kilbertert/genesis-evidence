"""Verbatim-excerpt grounding: the textual half of "say only what you can point at".

One normalization, one containment predicate, and one document flattener, shared by
ingestion-time grounding (``literature.ai_extraction``) and adjudication-time grounding
(``review.service``). The numeric half of the same invariant is
``core.metrics.evidence_contains_value``.

This module is pure: standard library only, no store, no JATS parser, no provider.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# How strictly a cited excerpt must appear in the source document.
#
#   strict     the excerpt must appear as-is.
#   citations  publisher-inserted inline figure/table markers are stripped from both sides
#              before the containment test, so a real quote the publisher decorated still
#              attests.
#
# Both modes are in use today: ingestion-time extraction tolerates the markers, source
# adjudication does not. Which mode *should* be correct is a separate decision; this module
# makes the difference an explicit argument instead of a consequence of which private helper
# a caller happened to import.
CitationTolerance = Literal["strict", "citations"]

_INLINE_CITATION_RE = re.compile(
    r"\((?:fig(?:ure)?|table|tbl\.?|appendix|supplement(?:ary)?)\b[^)]{0,160}\)",
    re.IGNORECASE,
)


def normalized_text(value: object) -> str:
    """NFKC, whitespace-collapsed, casefolded — the one spelling of normalization."""

    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


@dataclass(frozen=True)
class Excerpt:
    """A cited excerpt plus the caller's label for it, used only in failure reporting."""

    field: str
    text: str


@dataclass(frozen=True)
class AttestationResult:
    """``excerpts`` split into those present verbatim in the document and those absent."""

    attested: tuple[Excerpt, ...]
    missing: tuple[Excerpt, ...]

    @property
    def ok(self) -> bool:
        return not self.missing


def attest(
    document: object,
    excerpts: Sequence[Excerpt],
    *,
    tolerance: CitationTolerance = "strict",
) -> AttestationResult:
    """Report which of ``excerpts`` appear verbatim in ``document``.

    Convenience for a one-shot call. A caller that tests several excerpts against the same
    document — or several groups of excerpts — should call :func:`segments_from_document`
    once and :func:`attest_segments` per group, so the document is flattened once instead
    of once per call.
    """

    return attest_segments(segments_from_document(document), excerpts, tolerance=tolerance)


def segments_from_document(document: object) -> tuple[str, ...]:
    """Flatten a nested document into normalized text segments.

    ``None``-valued leaves become ``""`` so a document's shape is preserved rather than
    silently collapsing; ``""`` is a substring of everything, so preserving it keeps the
    verdicts this walk produced before it was shared.
    """

    return tuple(normalized_text(value) for value in _string_values(document))


def attest_segments(
    segments: Sequence[str],
    excerpts: Sequence[Excerpt],
    *,
    tolerance: CitationTolerance = "strict",
) -> AttestationResult:
    """Report which of ``excerpts`` appear verbatim in already-normalized ``segments``.

    Excerpts are matched independently, so this is a map from excerpt to verdict. A caller
    that needs an all-or-nothing gate over a group of excerpts (every fragment of one issue
    must attest) checks ``result.ok`` on that group — that policy stays at the caller.
    """

    attested: list[Excerpt] = []
    missing: list[Excerpt] = []
    for excerpt in excerpts:
        found = any(
            _excerpt_in_segment(excerpt.text, segment, tolerance=tolerance)
            for segment in segments
        )
        (attested if found else missing).append(excerpt)
    return AttestationResult(attested=tuple(attested), missing=tuple(missing))


def _excerpt_in_segment(excerpt: str, segment: str, *, tolerance: CitationTolerance) -> bool:
    # An excerpt that normalizes to "" attests vacuously. Every current caller's excerpts
    # come from a non-empty validated field, so this is reachable only by a caller that
    # passes an unvalidated string; treating it as a miss would change today's verdicts.
    # ponytail: no empty/malformed reason code until a caller can actually produce one.
    normalized_excerpt = _citation_spacing(normalized_text(excerpt))
    if normalized_excerpt in segment:
        return True
    if tolerance == "strict":
        return False
    without_citation = _citation_spacing(_INLINE_CITATION_RE.sub(" ", normalized_excerpt))
    segment_without_citation = _citation_spacing(_INLINE_CITATION_RE.sub(" ", segment))
    return without_citation in segment_without_citation


def _citation_spacing(value: str) -> str:
    return re.sub(r"\s+([,.;:!?])", r"\1", value)


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _string_values(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _string_values(item)]
    return []
