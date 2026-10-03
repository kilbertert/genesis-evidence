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

from collections.abc import Container, Mapping
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


#: The risk-of-bias instruments an appraisal may name. The review boundary's tool
#: type is derived from this, so a new instrument is one edit.
RISK_OF_BIAS_TOOLS: tuple[str, ...] = (
    "rob2",
    "robins_i",
    "robis",
    "amstar2",
    "diagnostic_accuracy",
    "exposure_study",
    "safety_signal",
    "other",
)

RiskOfBiasTool = Literal[*RISK_OF_BIAS_TOOLS]

#: Which instrument appraises each design. This was two tables: the review gate
#: enforced (design, tool) compatibility from one, and the review *suggestion* was
#: seeded from another with a different shape. The pair still exists — a design permits
#: a set, and the suggestion proposes one member — but both now read this table, so a
#: design cannot be seeded with an instrument its own gate rejects.
#:
#: A design absent here has **no compatibility rule**: the gate accepts any instrument,
#: which is the behaviour the gate already had for those designs.
_PERMITTED_RISK_OF_BIAS_TOOLS: Mapping[str, frozenset[str]] = {
    "randomized_controlled_trial": frozenset({"rob2"}),
    "systematic_review_meta_analysis": frozenset({"robis", "amstar2"}),
    "non_randomized_controlled_study": frozenset({"robins_i"}),
    "natural_experiment": frozenset({"robins_i"}),
    "biomarker_validation_study": frozenset({"diagnostic_accuracy"}),
    "bioavailability_pharmacokinetic_study": frozenset({"other"}),
    "controlled_feeding_metabolic_study": frozenset({"rob2", "other"}),
    "cohort_study": frozenset({"exposure_study"}),
    "case_control_study": frozenset({"exposure_study"}),
    "cross_sectional_study": frozenset({"exposure_study"}),
    "ecological_study": frozenset({"exposure_study"}),
    "case_series": frozenset({"safety_signal"}),
    "case_report": frozenset({"safety_signal"}),
    "animal_study": frozenset({"other"}),
    "in_vitro_study": frozenset({"other"}),
}

#: The instrument the review *suggestion* proposes. Every design is a key, so the
#: default is declared rather than fallen into: the previous code used a
#: ``dict.get(design, "other")`` whose probe happens to be permitted for every design
#: it caught, an invariant nothing enforced until now.
DEFAULT_RISK_OF_BIAS_TOOL: Mapping[str, str] = {
    **{design: "other" for design in STUDY_DESIGNS},
    "randomized_controlled_trial": "rob2",
    "systematic_review_meta_analysis": "robis",
    "non_randomized_controlled_study": "robins_i",
    "natural_experiment": "robins_i",
    "biomarker_validation_study": "diagnostic_accuracy",
    "cohort_study": "exposure_study",
    "case_control_study": "exposure_study",
    "cross_sectional_study": "exposure_study",
    "ecological_study": "exposure_study",
    "case_series": "safety_signal",
    "case_report": "safety_signal",
}

#: Designs that assign the exposure, so a synthesis of them starts at the top tier.
RANDOMIZED_DESIGNS: frozenset[str] = frozenset(
    {
        "randomized_controlled_trial",
        "systematic_review_meta_analysis",
        "controlled_feeding_metabolic_study",
    }
)

#: Designs that already synthesise several studies, so a single one of them is not a
#: single-study synthesis and its certainty is not capped on that ground.
SYNTHESIS_DESIGNS: frozenset[str] = frozenset({"systematic_review_meta_analysis"})


def permitted_risk_of_bias_tools(design: str) -> frozenset[str] | None:
    """The instruments a review may pick for a design, or ``None`` for no rule.

    ``None`` means the gate imposes no compatibility constraint, which is how every
    design outside the table already behaved. Callers must test for it rather than
    treating it as the empty set.
    """

    return _PERMITTED_RISK_OF_BIAS_TOOLS.get(design)


def risk_of_bias_tool_for(design: str) -> str:
    """The instrument to propose for a design.

    The one place a fallback survives, for a design outside the vocabulary (e.g. the
    extractor's own ``"uncertain"`` probe reaching an unpopulated path).
    """

    return DEFAULT_RISK_OF_BIAS_TOOL.get(design, "other")


def is_single_primary_capped(study_count: int, designs: Container[str]) -> bool:
    """Whether a synthesis is capped at low certainty for resting on one primary study.

    Takes the paper count and the design set separately: ``study_count == 1`` counts
    papers while ``len(designs) == 1`` counts distinct designs, and one paper of one
    design is not the same case as one paper that synthesises others.
    """

    return study_count == 1 and not (set(designs) & SYNTHESIS_DESIGNS)
