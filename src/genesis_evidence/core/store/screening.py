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


# NOT unified here, deliberately: `_require_complete_topic` calls a duplicate-run matrix
# *conflicting* when
#   count(DISTINCT COALESCE(full_text_decision, title_abstract_decision)) > 1
# while `reconcile_topic_ledger` compares the set of title_abstract_decisions and the set of
# full_text_decisions separately. Those disagree on a reachable matrix — one run with
# title_abstract=included (full text undecided) beside another with full_text=excluded is a
# conflict under the first definition and not under the second. Unifying them changes an
# observable outcome, so it needs its own decision and its own evidence; both keep their
# current definitions here.
