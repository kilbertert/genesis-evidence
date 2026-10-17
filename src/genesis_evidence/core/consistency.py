"""One owner of the dual-extraction verdict vocabulary and adjudication provenance.

Three decisions live here, all previously spread across layers:

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

**Actor provenance.** The same question — "did a machine write this?" — asked of the second
evidence channel: the `reviewer` column the store recorded. Kept here rather than in a
module of its own because the two channels answer one question and can already disagree
about one paper; naming the divergence is the point, and a five-line prefix list does not
need its own file.
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


#: The actor prefixes the autonomous pipeline writes into a `reviewer` column. Two of them
#: exist for a reason, and the reason decides which predicate a reader may use.
#:
#: `AUTOMATIC_ACTOR_PREFIX` is the pipeline's own signature — `review/service.py` builds it as
#: `f"ai:{model}"` for every event, admission and claim decision it makes, *without* having
#: checked anything against the full text.
#:
#: `SOURCE_ADJUDICATED_ACTOR_PREFIX` is the *source-adjudication* branch's signature
#: (`_source_adjudication` → the claim review at `review/service.py:567`). It is equally a
#: machine, but it was deliberately given a distinguishable actor, and the distinction carries
#: a fact: this rejection was verified against the paper's own full text. It is the only
#: machine actor that may be, so a predicate asked "may the machine revisit this unattended?"
#: must answer **no** for it — see :func:`is_autonomous_reopenable`. Folding it into the
#: automatic set would let the second pass flip a verified rejection to `approved` without
#: re-verifying anything, because the stored `source_based` resolution short-circuits
#: `_source_adjudication` (`review/service.py:396`).
#:
#: `system:` is deliberately *not* declared here. The store writes `system:ingestion` and
#: `system:migration`, but only into `full_text_retrieval_reviewer` and migration bookkeeping —
#: never into the three columns the readers test (`full_text_reviewer`,
#: `paper_admissions.reviewer`, `claim_reviews.reviewer`). Declaring a prefix no reader can
#: receive would be a claim of ownership the code does not honour; it becomes a real
#: entry when a value in that namespace reaches a reader column, with its own evidence.
AUTOMATIC_ACTOR_PREFIX = "ai:"
SOURCE_ADJUDICATED_ACTOR_PREFIX = "source-ai:"

#: The set :func:`is_machine_actor` reads. Both are machines; a prefix absent from this
#: tuple is a person's name from `GENESIS_EVIDENCE_REVIEWER_ID`, which is deliberately free
#: form — there is no closed vocabulary of people, so this is a prefix test and not a parse.
MACHINE_ACTOR_PREFIXES: tuple[str, ...] = (
    AUTOMATIC_ACTOR_PREFIX,
    SOURCE_ADJUDICATED_ACTOR_PREFIX,
)

#: The subset whose decisions the autonomous pass may revisit on a later run *without*
#: re-verifying them. Exactly one prefix: the pipeline's own unverified writes.
#:
#: `source-ai:` is deliberately absent. Its rejection was checked against the full text once,
#: on the pass that wrote it; after that the stored `source_based` resolution stops
#: `_source_adjudication` from running again (`review/service.py:396`), so a reopen would move
#: the claim to `approved` on the strength of a verification that is no longer taking place.
#: That is why the pre-existing behavioural baseline pins a `source-ai:` rejection as NOT
#: reopened — see `test_ai_source_adjudication_rejects_a_statistically_contradictory_claim_once`
#: in `tests/test_review_workflow.py`. A machine may not silently overturn a machine.
AUTONOMOUS_REOPENABLE_PREFIXES: tuple[str, ...] = (AUTOMATIC_ACTOR_PREFIX,)


def is_machine_actor(actor: object) -> bool:
    """Whether a stored `reviewer`/`actor` value was written by a machine.

    A classification, not a policy: it says who acted, and nothing about what may follow. A
    blank, absent or `NULL` value is **not** a machine — an unattributed row is not evidence
    of an autonomous decision, and a caller that needs to treat it separately must say so
    rather than inherit it from `or ""`.
    """

    return str(actor or "").strip().startswith(MACHINE_ACTOR_PREFIXES)


def is_autonomous_reopenable(actor: object) -> bool:
    """Whether the autonomous pass may revisit a decision this actor made, unattended.

    Stricter than :func:`is_machine_actor` on purpose: a source-adjudicated rejection is a
    machine's, but re-opening it would overturn a verdict the pass can no longer re-derive.
    The two predicates existing separately is what lets both facts be stated.
    """

    return str(actor or "").strip().startswith(AUTONOMOUS_REOPENABLE_PREFIXES)


def automatic_actor_for_model(model: object) -> str:
    """The autonomous pipeline's actor string for `model`, in one place.

    `review/service.py` builds this by hand for every event, admission and claim decision it
    writes; the prefix and its consumer must not be able to drift apart.
    """

    return f"{AUTOMATIC_ACTOR_PREFIX}{model}"


def source_adjudicated_actor_for_model(model: object) -> str:
    """The source-adjudication branch's actor string for `model`."""

    return f"{SOURCE_ADJUDICATED_ACTOR_PREFIX}{model}"
