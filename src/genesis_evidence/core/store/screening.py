"""One owner of the collection-screening predicates and the profile-immutability probe.

The screening state of a (topic, paper) pair is asked in fifteen SQL fragments across
``papers.py`` and ``review.py``, in three vocabularies that encode three different defaults:

``included``      the column must explicitly say ``included``; an undecided paper is **not**
                  eligible. Used by the evidence-body paths — admission, cards, profiles,
                  scope candidates, and the PRISMA inclusion counter.
``not_excluded``  an undecided value counts as included; only an explicit ``excluded``
                  fails. Used by the extraction-job machinery, at both stages.
``excluded_any``  the full-text decision wins, falling back to title/abstract. Used by the
                  exclusion-reason check and conflict detection.

Those three are deliberately **not** unified. They disagree on purpose: a paper whose
screening is still undecided may already hold an extraction job yet must not reach a card.
Changing which default applies where is a behavioural change with its own evidence, not
this refactor's job. What this module removes is drift in *spelling* — a decision value
renamed in one call site and missed in its eleven siblings.

This module also owns the duplicate-run **conflict definition** (below), which used to be
spelled two different ways in the ledger reconciler and the topic-closure gate.

Each helper takes the SQL alias its caller uses, so call sites keep their own FROM/JOIN
shape and only the predicate is shared.
"""

from __future__ import annotations

INCLUDED = "included"
EXCLUDED = "excluded"


def included_sql(column: str) -> str:
    """``included``: ``column`` must explicitly equal ``included``."""

    return f"{column} = '{INCLUDED}'"


def not_excluded_sql(column: str) -> str:
    """``not_excluded``: an undecided ``column`` counts as included."""

    return f"COALESCE({column}, '{INCLUDED}') <> '{EXCLUDED}'"


def excluded_any_stage_sql(full_text_column: str, title_abstract_column: str) -> str:
    """``excluded_any``: excluded at full text, falling back to title/abstract.

    A different COALESCE from :func:`not_excluded_sql` — this one collapses the *two stages*
    rather than filling one undecided value.
    """

    return f"COALESCE({full_text_column}, {title_abstract_column}) = '{EXCLUDED}'"


def paper_used_by_profile_sql() -> str:
    """SQL selecting ``1`` when the paper already backs a result in the topic's profile.

    Screening and full-text retrieval are both refused once a paper is inside a profile:
    changing its screening afterwards would rewrite evidence a profile was built from. Two
    call sites spelled this join chain out identically.
    """

    return """
        SELECT 1
        FROM evidence_profile_results epr
        JOIN results r ON r.id = epr.result_id
        JOIN claims c ON c.result_id = r.id
        JOIN evidence_profiles ep ON ep.id = epr.profile_id
        WHERE ep.topic_id = ? AND c.paper_id = ?
        LIMIT 1
    """


def profiled_papers_sql() -> str:
    """SQL listing the paper ids a topic's profiles already cover.

    The listing form of the same walk, for ledger reconciliation, which needs the whole set
    rather than a membership probe.
    """

    return """
        SELECT DISTINCT c.paper_id
        FROM evidence_profile_results epr
        JOIN evidence_profiles ep ON ep.id = epr.profile_id
        JOIN results r ON r.id = epr.result_id
        JOIN claims c ON c.result_id = r.id
        WHERE ep.topic_id = ?
    """


# --- the duplicate-run conflict definition (one owner) ---------------------------------
#
# "Conflicting" is defined once, using the *collapsed* value: a paper's decision for a run is
# its full-text decision if it has one, otherwise its title/abstract decision. Two completed
# runs conflict when that collapsed value differs between them. In SQL:
#
#   count(DISTINCT COALESCE(full_text_decision, title_abstract_decision)) > 1
#
# Why this and not the obvious alternative of comparing the title set and the full-text set
# separately: that alternative misses a real disagreement. A run that reached
# full-text `excluded` and a run that never got past an undecided full text are, *for the
# paper*, two different conclusions — the second says the screening never reached a verdict,
# the first says it reached "exclude". Under the separate-set rule the second run's `NULL`
# simply drops out of the set, the two look identical, and reconciliation then copies
# `full_text_decision = 'excluded'` onto a run that was never retrieved — asserting a
# full-text verdict for evidence nobody screened. The collapsed rule treats the undecided run
# as its own conclusion and refuses to guess, which is the fail-closed direction.
#
# The two rules disagree on 144 of the reachable duplicate-run matrices (exhaustive
# enumeration; 64 of those have closure's conflict branch as the only blocker). Recorded in
# #208; this module is the single owner of the chosen rule.


def screening_conflict_sql(
    paper_id_column: str,
    title_abstract_column: str,
    full_text_column: str,
) -> str:
    """SQL ``HAVING``/predicate body that is true when a paper's runs conflict.

    Consumed by both the ledger reconciler and the topic-closure gate, so the two cannot
    disagree about which matrices conflict. The caller supplies the grouped column names.
    """

    return (
        f"count(DISTINCT COALESCE({full_text_column}, {title_abstract_column})) > 1"
    )


def collapsed_decision(full_text_decision: object, title_abstract_decision: object) -> object:
    """The paper's single decision for one run: full text if present, else title/abstract.

    The Python form of the ``COALESCE`` above, so the reconciler groups runs exactly the way
    the SQL predicate does.
    """

    return full_text_decision if full_text_decision is not None else title_abstract_decision

