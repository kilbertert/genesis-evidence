"""One owner of the rule "what evidence may support a patient-visible card".

Card drafting (``create_card``), the publish gate (``_require_publishable``), the coverage
matrix's eligibility subquery, and the scope-candidate projection each spelled the same
eligibility criteria out by hand — five copies of the excluded-study-design set, five of the
intervention-effect rule, two of the unresolved-risk set. A corrected criterion landing in
one surface and not another is patient-visible: a card can be drafted, shown as
"ready to publish", or blocked inconsistently from identical evidence.

The values below are the whole rule. They are the only definitions; every eligibility site
interpolates them, so changing what may support a card is one edit here.

These are internal constants, never caller input — :func:`sql_values` renders them into a
statement, which is why it must only ever be handed the tuples defined in this module.
"""

from __future__ import annotations

# Study designs that describe mechanism or an individual case rather than an effect
# estimate a patient-facing claim could rest on. The design *vocabulary* is owned by
# `core.methodology`; this is the eligibility criterion over it, and
# `tests/test_methodology.py` asserts every design named here is a real one.
EXCLUDED_STUDY_DESIGNS: tuple[str, ...] = (
    "animal_study",
    "in_vitro_study",
    "case_series",
    "case_report",
)

# The only claim type a patient-visible card may rest on: a direct intervention effect.
CARD_CLAIM_TYPE = "intervention_effect"

# Risk-of-bias verdicts that are not "resolved". A card at low certainty tolerates these;
# high or moderate certainty does not, and the publish gate rejects them outright.
UNRESOLVED_RISK_LEVELS: tuple[str, ...] = ("high", "critical", "uncertain")

# `clear` is no longer left unowned here: the paper-level integrity vocabulary — which
# statuses admit a paper and which forbid durable evidence — is owned by
# `core.publication_integrity` (#264), which every eligibility site interpolates. The
# remaining paper-level token below is a *documented shared duplicate*.
#
# Deliberately NOT owned here: the publication status `formal` and the admission status
# `internally_admitted`. Both are single-token values that also appear in the admission
# state machine, the workbench guidance, and the extraction worker — not only in card
# eligibility. Declaring them here without rewiring all of those would be a claim of
# ownership that changing the constant would not honour, which is worse than leaving them
# where they are. `formal` travels beside integrity in the same queries; `#264` explicitly
# leaves the `formal`/`preprint`/`unknown` vocabulary to its own owner, and those four
# `= 'formal'` sites are guarded by the eligibility sites that render eligibility from
# this module instead of restating the whole predicate.


def sql_values(values: tuple[str, ...]) -> str:
    """Render an internal constant tuple as a SQL value list.

    Only for the constants in this module, which are literals defined above; the quoting is
    presentation, not sanitisation, and nothing caller-supplied may reach it.
    """

    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)
