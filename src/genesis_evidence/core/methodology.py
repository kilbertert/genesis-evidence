"""One owner of the evidence-appraisal taxonomy and the inference constraint.

Two bodies of knowledge live here, both previously restated layer by layer.

**The study-design vocabulary.** The 18 designs an appraisal can name were spelled
out six times: as a ``Literal`` on the extraction model, as a second hand-copied
``Literal`` at the review boundary, as a SQL ``CHECK`` on ``claim_reviews`` in both
the current schema and the legacy-rebuild migration, and twice in the review
workbench's browser code. :data:`STUDY_DESIGNS` is the one Python definition, and
:data:`StudyDesign` is *derived* from it, so the extraction model and the review
boundary cannot diverge. Adding a design to extraction makes it reviewable in the
same edit.

**The inference constraint.** Which designs may carry a causal claim used to be
written three times, once per caller policy: the extraction validator *rejects*
provider output, the review gate *rejects* reviewer input, and the autonomous
reviewer *rewrites* causal to associational. Three encodings of one rule, only one
changeable at a time. :func:`permits_causal_inference` owns the rule; each caller
keeps its own policy and its own message.

**This inference is a recognised limit, not a guarantee.** The rule below is the
only one the layers share. The *consequences* differ deliberately and are not
owned here — a future tightening of "which designs are observational" changes all
three callers at once, but a future change to *how* one caller reacts stays that
caller's to make.

**What cannot be single-sourced.** A schema string cannot import Python, and
neither can a browser. The two ``corrected_study_design`` ``CHECK`` constraints and
the two workbench lists therefore stay literal. They are not unguarded: an edit
that lets them drift from :data:`STUDY_DESIGNS` fails
``tests/test_methodology.py``, which extracts them from the source and compares
the value sets. That is a guarded duplicate, which is the honest ceiling here.

This module imports nothing from ``core.store``, ``review``, or ``literature``, so
all three layers may depend on it without an import cycle — the discipline
``core.consistency`` and ``review.scope`` already follow.
"""

from __future__ import annotations

from typing import Literal

#: The one Python study-design vocabulary. Ordered as the original literal was, so
#: the derived :data:`StudyDesign` is unchanged for every existing caller.
STUDY_DESIGNS: tuple[str, ...] = (
    "randomized_controlled_trial",
    "systematic_review_meta_analysis",
    "cohort_study",
    "case_control_study",
    "cross_sectional_study",
    "controlled_feeding_metabolic_study",
    "bioavailability_pharmacokinetic_study",
    "biomarker_validation_study",
    "non_randomized_controlled_study",
    "natural_experiment",
    "ecological_study",
    "animal_study",
    "in_vitro_study",
    "case_series",
    "case_report",
    "guideline",
    "other",
    "uncertain",
)

#: The review boundary's design type, derived so it cannot drift from the tuple above.
StudyDesign = Literal[*STUDY_DESIGNS]

#: Designs that observe exposure rather than assign it, so an effect they measure is an
#: association. Drafting and approving a causal claim on one of these is the error the
#: :func:`permits_causal_inference` callers exist to prevent.
OBSERVATIONAL_DESIGNS: frozenset[str] = frozenset(
    {
        "cohort_study",
        "case_control_study",
        "cross_sectional_study",
        "case_series",
        "case_report",
        "ecological_study",
    }
)


def permits_causal_inference(design: str) -> bool:
    """Whether a design may carry a causal claim.

    The predicate the extraction validator, the review gate, and the autonomous
    reviewer all ask. It is deliberately narrow: the module owns *whether* causal
    is permitted, never *what a caller does* about it.
    """

    return design not in OBSERVATIONAL_DESIGNS
