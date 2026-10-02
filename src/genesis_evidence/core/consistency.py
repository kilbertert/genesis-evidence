"""One owner of the dual-extraction verdict vocabulary and adjudication provenance.

Two decisions live here, both previously spread across layers:

**The verdict vocabulary.** Whether two independent extractions agree is spelled
``'consistent' | 'needs_review'`` in the extractor's report model and again in the
``consistency_status`` column's CHECK. The values are named here so the Python side has one
source; the SQL CHECK stays a literal because a schema string cannot import one.

**Adjudication provenance.** When the two extractions disagree, something has to clear the
difference before the paper may be admitted. Three producers write a resolution *sentence*,
and whether that sentence counts as a source-based adjudication used to be decided by a
private predicate living in ``core.store.review`` and imported cross-package by
``review.service`` — a private symbol reached into from another layer.

Only the resolution **text** is persisted (``paper_admissions.consistency_resolution``); no
column records who produced it. So provenance must be *inferred from the text* on every
read, and this module owns that inference in one place.

**This inference is a recognised limit, not a guarantee.** A resolution is classified
source-based unless it opens with :data:`AUTOMATIC_RESOLUTION_PREFIX`. That means any stored
text — including a human's free-text override typed into the workbench textarea — that
happens to begin with the automatic sentence's opening is read as *automatic*. Narrowing
that would mean persisting the producer rather than re-deriving it, which is a schema change
this module deliberately does not make; see :func:`classification_is_text_inferred`.
"""

from __future__ import annotations

from typing import Literal

Verdict = Literal["consistent", "needs_review"]
ResolutionSource = Literal["source_based", "automatic", "none"]

CONSISTENT: Verdict = "consistent"
NEEDS_REVIEW: Verdict = "needs_review"

#: The opening of the sentence `review.service._automatic_resolution` writes. A resolution
#: starting with this is read back as an *automatic* adjudication — the one the autonomous
#: reviewer may clear the `dual_ai` blocker for without a human signature.
AUTOMATIC_RESOLUTION_PREFIX = "AI consistency adjudication ("


def adjudication_source(resolution: object) -> ResolutionSource:
    """Classify a stored resolution's provenance from its text.

    ``"none"`` when nothing was recorded; ``"automatic"`` when the sentence opens with
    :data:`AUTOMATIC_RESOLUTION_PREFIX`; ``"source_based"`` otherwise (which includes a
    human override, since the text does not distinguish them).

    The truth table reproduces the predicate it replaces exactly, including the empty case:
    an absent or blank resolution is ``"none"``, and therefore *not* source-based.
    """

    text = str(resolution or "").strip()
    if not text:
        return "none"
    if text.startswith(AUTOMATIC_RESOLUTION_PREFIX):
        return "automatic"
    return "source_based"


def is_source_based(resolution: object) -> bool:
    """Whether the resolution counts as a source-based adjudication.

    The one predicate the admission gate and the autonomous reviewer both ask. Kept as a
    named wrapper so no caller re-tests the text itself.
    """

    return adjudication_source(resolution) == "source_based"


def is_automatic(resolution: object) -> bool:
    """Whether the resolution is the autonomous reviewer's own adjudication."""

    return adjudication_source(resolution) == "automatic"


def classification_is_text_inferred() -> str:
    """Why provenance is inferred rather than stored, for callers that must know the limit."""

    return (
        "Provenance is derived from the resolution text because only the text is persisted. "
        "Narrowing this requires storing the producer alongside it."
    )
