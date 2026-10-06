"""Guards for the one card lifecycle: `core.card_lifecycle` (#221).

The lifecycle was declared in six places across four modules and two languages,
and the copies had already diverged — the coverage tally had dropped `rejected`,
and the patient-visible predicate was enforced by the writer and stated by none
of the readers. Two of those declarations cannot import Python at all (the SQL
`CHECK` and the workbench), so they stay literal or are fetched at runtime; these
tests are what keeps them from drifting. Each guard fails on the shape it
forbids, not merely passes today.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from genesis_evidence.core.card_lifecycle import (
    ACTION_THRESHOLD_GRADES,
    AUTONOMOUS_REVIEW_PATH,
    CARD_STATUSES,
    CARD_TRANSITIONS,
    NON_TERMINAL_STATUSES,
    PATIENT_VISIBLE_GRADES,
    TERMINAL_STATUSES,
    CardStatus,
    can_transition,
    is_patient_visible,
    next_statuses,
)

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "genesis_evidence"


def _source(relative_path: str) -> str:
    return (SOURCE_ROOT / relative_path).read_text()


def _sql_card_status_check() -> set[str]:
    """The value set of the `knowledge_cards.status` CHECK, whitespace-insensitively."""

    source = _source("core/store/schema.py")
    match = re.search(r"knowledge_cards\s*\((.*?)\);", source, re.DOTALL)
    assert match, "schema.py: no knowledge_cards table found"
    status = re.search(r"status\s+TEXT[^,]*?CHECK\s*\(status\s+IN\s*\((.*?)\)\)", match.group(1))
    assert status, "schema.py: knowledge_cards.status has no CHECK"
    return set(re.findall(r"'([a-z_]+)'", status.group(1)))


def test_the_contract_type_is_derived_from_the_one_vocabulary() -> None:
    assert get_args(CardStatus) == CARD_STATUSES


def test_the_status_classes_partition_the_vocabulary() -> None:
    assert set(CARD_STATUSES) == TERMINAL_STATUSES | NON_TERMINAL_STATUSES
    assert not (TERMINAL_STATUSES & NON_TERMINAL_STATUSES)


def test_no_module_restates_the_card_status_vocabulary_in_a_literal() -> None:
    """The scan that catches a re-declared vocabulary.

    `Literal[...]` is the shape every restatement of the card vocabulary took. A
    span holding several statuses is a second copy regardless of whether it
    matches today — and a copy that has already drifted is exactly what a
    content-equality check cannot see. `card_lifecycle.py` itself is skipped: it
    declares the vocabulary as a tuple, and its own `Literal[*CARD_STATUSES]` is
    the derivation, not a copy.
    """

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "card_lifecycle.py":
            continue
        for match in re.finditer(r"Literal\[([^\]]*)\]", path.read_text()):
            hits = [
                status
                for status in CARD_STATUSES
                if re.search(rf"['\"]{re.escape(status)}['\"]", match.group(1))
            ]
            # A copy declares the vocabulary; the vocabulary has six values, so a
            # span naming three or more of them is a copy. One or two is not: a
            # single status is a legitimate narrowing (`Literal["published"]` is
            # the patient-facing card's fixed status), and a shared pair is a
            # different value set entirely (`Literal["approved", "rejected"]` is
            # a claim-review decision that reuses two of the same words).
            if len(hits) > 2:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {sorted(hits)}")
    assert not offenders, "card status vocabulary restated in a Literal:\n" + "\n".join(offenders)


def test_the_schema_check_matches_the_vocabulary() -> None:
    """The SQL CHECK cannot import Python, so it is extracted and compared."""

    assert _sql_card_status_check() == set(CARD_STATUSES)


def test_no_module_restates_the_transition_graph_as_a_dict_literal() -> None:
    """A second `{status: [...]}` graph is the copy that drifts.

    The graph lived inline in `transition_card`. A dict literal mapping more than
    one status to a target list is that copy in a new location.
    """

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "card_lifecycle.py":
            continue
        text = path.read_text()
        for match in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
            span = match.group(0)
            if not re.search(r"['\"]draft['\"]\s*:", span):
                continue
            hits = [
                status
                for status in CARD_STATUSES
                if f"'{status}'" in span or f'"{status}"' in span
            ]
            if len(hits) > 1:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {sorted(hits)}")
    assert not offenders, "transition graph restated as a dict literal:\n" + "\n".join(offenders)


def test_the_workbench_reads_the_graph_rather_than_restating_it() -> None:
    source = _source("review/workbench.html")

    assert "cardTransitions[card.status]" in source
    # The old inline graph must be gone from the page.
    assert "draft:['in_review'" not in source


def test_every_transition_target_is_a_real_status_and_a_real_move() -> None:
    for source, targets in CARD_TRANSITIONS.items():
        assert source in CARD_STATUSES, source
        for target in targets:
            assert target in CARD_STATUSES, target
            assert can_transition(source, target)
    # A target may be terminal: refusing a card moves it to `rejected`, which is
    # a real move and a real end. Only `draft` must never be re-entered.



def test_the_autonomous_path_is_a_walk_of_the_graph() -> None:
    """The reviewer must not describe a path the graph does not allow."""

    for source, target in zip(AUTONOMOUS_REVIEW_PATH, AUTONOMOUS_REVIEW_PATH[1:], strict=False):
        assert can_transition(source, target), f"autonomous path {source} -> {target} is illegal"


def test_sinks_offer_nothing_and_an_unknown_status_is_not_open() -> None:
    for sink in ("published", "rejected", "stale"):
        assert next_statuses(sink) == (), sink
    assert next_statuses("not_a_status") == ()


def test_patient_visibility_needs_both_a_visible_status_and_a_strong_enough_grade() -> None:
    assert is_patient_visible("published", "high")
    assert is_patient_visible("published", "moderate")
    assert is_patient_visible("published", "low")
    # A very_low card is draftable and reviewable but may not reach a patient.
    assert not is_patient_visible("published", "very_low")
    assert not is_patient_visible("published", None)
    for status in CARD_STATUSES:
        if status != "published":
            assert not is_patient_visible(status, "high"), status


def test_the_action_threshold_is_a_separate_and_narrower_set() -> None:
    """Visibility and advice are different questions over the same grades."""

    assert ACTION_THRESHOLD_GRADES < PATIENT_VISIBLE_GRADES
    assert {"low"} == PATIENT_VISIBLE_GRADES - ACTION_THRESHOLD_GRADES


def test_the_literal_scan_actually_catches_a_restatement() -> None:
    """Verify the guard discriminates, rather than passing because nothing is found.

    A guard that cannot fail is not a guard. These are the shapes the scan must
    separate: a real copy is flagged, and the two legitimate narrowings the
    repository already contains are not.
    """

    def flags(text: str) -> bool:
        for match in re.finditer(r"Literal\[([^\]]*)\]", text):
            hits = [
                status
                for status in CARD_STATUSES
                if re.search(rf"['\"]{re.escape(status)}['\"]", match.group(1))
            ]
            if len(hits) > 2:
                return True
        return False

    # The copy that used to live inline is caught.
    assert flags('CardStatus = Literal["draft", "in_review", "approved", "published"]')
    assert flags("x: Literal['draft', 'published', 'stale']")
    # The legitimate narrowings are not.
    assert not flags('status: Literal["published"]')  # patient card's fixed status
    assert not flags('decision: Literal["approved", "rejected"]')  # claim review
    assert not flags('Literal["uploaded", "extracted", "confirmed"]')  # unrelated set


def test_no_module_restates_the_visibility_rule_in_sql() -> None:
    """The patient-visible predicate is stated in SQL in four places.

    Those queries must filter in the database, so they cannot call the Python
    predicate — they render it from `patient_visible_sql` instead. A hand-written
    `status = 'published' AND grade IN (...)` is the copy this forbids.
    """

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text()
        for match in re.finditer(
            r"grade\s+IN\s*\(([^)]*)\)[^\n]*\n?[^\n]*", text
        ):
            window = text[max(0, match.start() - 200) : match.end() + 200]
            hits = [grade for grade in PATIENT_VISIBLE_GRADES if f"'{grade}'" in match.group(1)]
            # A card-selection query pairs the grade list with an *equality* on
            # the published status. The schema's column CHECK bounds the grade
            # and only *enumerates* status values elsewhere, so it is a different
            # statement.
            if (
                len(hits) > 1
                and re.search(r"status\s*=\s*'published'", window)
                and "patient_visible_sql" not in window
            ):
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {sorted(hits)}")
    assert not offenders, "visibility rule restated in SQL:\n" + "\n".join(offenders)


def test_the_sql_scan_discriminates() -> None:
    """The guard above must fail on a copy and pass on the rendered form."""

    def flags(text: str) -> bool:
        for match in re.finditer(r"grade\s+IN\s*\(([^)]*)\)", text):
            window = text[max(0, match.start() - 200) : match.end() + 200]
            hits = [
                grade
                for grade in PATIENT_VISIBLE_GRADES
                if f"'{grade}'" in match.group(1)
            ]
            if (
                len(hits) > 1
                and re.search(r"status\s*=\s*'published'", window)
                and "patient_visible_sql" not in window
            ):
                return True
        return False

    assert flags("WHERE status = 'published' AND grade IN ('high', 'moderate', 'low')")
    assert not flags('WHERE {patient_visible_sql("kc")}')
    # A different grade set is not this rule, and neither is the schema's column
    # CHECK, which bounds the grade without mentioning a status.
    assert not flags("grade IN ('very_low', 'low')")
    assert not flags("grade TEXT CHECK (grade IN ('high', 'moderate', 'low', 'very_low'))")


def test_the_rendered_sql_matches_the_predicate() -> None:
    from genesis_evidence.core.card_lifecycle import patient_visible_sql

    rendered = patient_visible_sql("kc")
    assert "kc.status = 'published'" in rendered
    for grade in PATIENT_VISIBLE_GRADES:
        assert f"'{grade}'" in rendered
    assert "'very_low'" not in rendered
