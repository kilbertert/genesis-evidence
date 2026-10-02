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
# A paper collected by several completed runs must be reconciled so the runs agree. That is
# only legitimate while the runs are genuinely the *same* screening record seen twice: one
# run carries a decision and the others are still blank. Once two runs reached **different
# stages**, each carries a partial history of its own, no "canonical" value exists, and
# reconciliation must refuse rather than invent one.
#
# A run's conclusion is therefore its stage pair (title_abstract_decision,
# full_text_decision) — not a single collapsed value. Two runs conflict when they carry two
# *different* conclusions. A run with no decision at all is not a conclusion; it is a blank
# that may be filled from a sibling, which is the ordinary duplicate-propagation case.
#
# Both simpler rules were tried and each fails on a reachable matrix (both recorded in #208
# and its follow-up):
#
#   comparing the two stage sets separately
#       run1 = (title included, full text undecided), run2 = (title included, full text
#       excluded) → the undecided run's NULL drops out of both sets, the two look identical,
#       and reconciliation copies `full_text_decision = 'excluded'` onto a run that was never
#       retrieved: a full-text verdict for evidence nobody screened.
#
#   collapsing the pair to `COALESCE(full_text, title_abstract)`
#       run A = (title excluded, full text undecided), run B = (title included, full text
#       excluded) → both collapse to 'excluded', so reconciliation overwrites both rows and
#       **erases run A's title-stage exclusion** together with its reason.
#
# The stage-pair rule catches both: the pairs differ in each case. It is a strict superset of
# the two, so it can only add refusals, never remove one — the fail-closed direction.


def screening_conflict_sql(
    paper_id_column: str,
    title_abstract_column: str,
    full_text_column: str,
) -> str:
    """SQL ``HAVING``/predicate body that is true when a paper's runs conflict.

    Consumed by both the ledger reconciler and the topic-closure gate, so the two cannot
    disagree about which matrices conflict. The caller supplies the grouped column names.

    A run with no decision at all collapses to SQL NULL and is skipped by
    ``count(DISTINCT ...)``, so an unscreened duplicate is a blank to fill, not a conflict.
    """

    del paper_id_column  # the caller already groups by it
    signature = (
        f"COALESCE({full_text_column}, '') || '\x1f' || "
        f"COALESCE({title_abstract_column}, '')"
    )
    return (
        "count(DISTINCT CASE WHEN "
        f"{full_text_column} IS NULL AND {title_abstract_column} IS NULL THEN NULL "
        f"ELSE {signature} END) > 1"
    )


def terminal_exclusion(collections: list[dict[str, object]]) -> bool:
    """True when every collection for a paper was excluded, with nothing left to suggest.

    The paper is then terminal: no full text will be acquired and no claim can be built, so
    the autonomous reviewer rejects it. Defined once because the reviewer acts on it and the
    workbench must *show* it — before this they were two ad-hoc predicates and the workbench
    showed nothing at all, leaving a terminally closed paper reading as "blocked" forever.

    **Every** collection counts, including one on a run still in progress: an open run can
    still reach a different conclusion, so a paper with one is not terminal. That is what
    keeps a half-finished collection from being reported as a finished paper.
    """

    if not collections:
        return False
    for collection in collections:
        excluded = (
            collection.get("title_abstract_decision") == EXCLUDED
            or collection.get("full_text_decision") == EXCLUDED
        )
        if not excluded:
            return False
        if (collection.get("screening_suggestion") or {}).get("stage"):
            return False
    return True


def run_conclusion(
    full_text_decision: object, title_abstract_decision: object
) -> tuple[object, object] | None:
    """The stage pair a run concluded, or ``None`` when the run decided nothing yet.

    The Python form of the SQL above, so the reconciler groups runs exactly the way the
    predicate does.
    """

    if full_text_decision is None and title_abstract_decision is None:
        return None
    return (title_abstract_decision, full_text_decision)


# --- the structured-results check (one owner) -------------------------------------------
#
# "Has this paper produced checkable structured evidence?" is asked by the review detail and
# must be answered the same way by the queue projection, which runs it in SQL. A paper with
# an extraction but no claims is not `ready_for_automation`: there is nothing to automate
# over, and `auto_review_paper` answers `attention_required`. Before this the queue fell
# through to `ready_for_automation` while the detail said `blocked` (#211).

RESULT_FIELDS: tuple[str, ...] = (
    "population",
    "ingredient_name",
    "ingredient_form",
    "dose",
    "comparator",
    "outcome",
    "timepoint",
    "effect_estimate",
    "statistical_details",
)

# Carried by the claim row itself rather than by its result.
CLAIM_FIELDS: tuple[str, ...] = ("result_id", "evidence_text", "locator")

# The whitespace a field may consist of and still count as empty. Spelled out because the
# two implementations must agree *exactly*: SQLite's `trim()` knows only the characters you
# hand it, while Python's `str.strip()` strips every character Unicode calls whitespace —
# including U+00A0 and friends that SQL has no compact way to name. Pinning both to this one
# set is what keeps them from disagreeing on a field holding a non-breaking space.
EMPTY_WHITESPACE = (" ", "\t", "\n", "\r", "\x0b", "\x0c")


def structured_results_sql(paper_id_expression: str) -> str:
    """SQL predicate true when a paper has claims and **none** of them is under-populated.

    Self-contained: it joins its own `claims`/`results`, so the caller only supplies the
    expression identifying the paper.

    "None incomplete", not "one complete": a paper with one good claim and one missing its
    dose is not checkable evidence, and the Python side requires every claim. Phrasing it as
    ``EXISTS (complete claim)`` accepted exactly that mixed case.
    """

    trim_characters = " || ".join(f"char({ord(character)})" for character in EMPTY_WHITESPACE)

    def present(alias: str, field: str) -> str:
        # coalesce: a missing result row leaves the field NULL, and `NULL <> ''` is NULL,
        # not true — which would let an incomplete claim escape the NOT.
        return f"trim(coalesce({alias}.{field}, ''), {trim_characters}) <> ''"

    # A claim whose result row is missing is incomplete too, hence the LEFT JOIN.
    required = [present("sc", field) for field in CLAIM_FIELDS] + [
        present("sr", field) for field in RESULT_FIELDS
    ]
    return (
        f"(EXISTS (SELECT 1 FROM claims sc WHERE sc.paper_id = {paper_id_expression})"
        " AND NOT EXISTS (SELECT 1 FROM claims sc"
        " LEFT JOIN results sr ON sr.id = sc.result_id"
        f" WHERE sc.paper_id = {paper_id_expression}"
        f" AND NOT ({' AND '.join(required)})))"
    )


def has_structured_results(claims: list[dict[str, object]]) -> bool:
    """The Python form: at least one claim, every one carrying all required fields.

    Mirrors :func:`structured_results_sql`. The detail loads each claim with its result's
    fields flattened onto the same dict, so one lookup covers both groups.

    Strips only :data:`EMPTY_WHITESPACE`, not whatever ``str.strip()`` happens to consider
    whitespace — otherwise a lone non-breaking space would read as empty here and as content
    in SQL.
    """

    if not claims:
        return False
    strippable = "".join(EMPTY_WHITESPACE)
    return all(
        all(
            str(claim.get(field) or "").strip(strippable)
            for field in (*CLAIM_FIELDS, *RESULT_FIELDS)
        )
        for claim in claims
    )

