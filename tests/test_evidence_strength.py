"""Guards for the one evidence-strength vocabulary: `core.evidence_strength` (#238).

The strength value set was declared eight times in Python, twice in SQL, and once
more in the workbench's browser code, and the copies had already come apart: the
workbench told the reviewer that `low` certainty is withheld from patients while
the publish gate ships it as a visible context card. The rank lived in the
matching module, the thresholds in the lifecycle module, and the labels in the
autonomous reviewer — three orderings over one value class, one of which raised a
bare `KeyError`.

Two of those declarations cannot import Python at all, so they stay literal or are
fetched at runtime; these tests are what keeps them from drifting. Each guard is
written to fail on the shape it forbids, not merely to pass today.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from genesis_evidence.core.card_lifecycle import (
    ACTION_THRESHOLD_GRADES,
    PATIENT_VISIBLE_GRADES,
)
from genesis_evidence.core.evidence_strength import (
    ACTION_THRESHOLD_STRENGTHS,
    CONTEXT_ONLY_STRENGTHS,
    EVIDENCE_RANK,
    EVIDENCE_STRENGTHS,
    MIXED,
    PATIENT_VISIBLE_STRENGTHS,
    STRENGTH_LABELS,
    CardGrade,
    EvidenceStrength,
    UnknownEvidenceStrength,
    evidence_strength_summary,
    rank,
    strength_for_score,
    strength_label,
    strength_vocabulary,
    visibility_guidance,
)

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "genesis_evidence"


def _source(relative_path: str) -> str:
    return (SOURCE_ROOT / relative_path).read_text()


def _sql_grade_check(relative_path: str, column: str) -> set[str]:
    """The value set of a `certainty`/`grade` CHECK, whitespace-insensitively."""

    source = _source(relative_path)
    match = re.search(rf"{column}\s+TEXT[^,]*?CHECK\s*\({column}\s+IN\s*\((.*?)\)\)", source)
    assert match, f"{relative_path}: no {column} CHECK found"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_contract_types_are_derived_from_the_one_vocabulary() -> None:
    assert get_args(CardGrade) == EVIDENCE_STRENGTHS
    assert get_args(EvidenceStrength) == (*EVIDENCE_STRENGTHS, MIXED)


def test_the_aggregate_is_not_a_member_of_the_base_vocabulary() -> None:
    """`mixed` describes several cards; no card can have it."""

    assert MIXED not in EVIDENCE_STRENGTHS
    assert MIXED == "mixed"


def test_the_rank_is_a_total_order_over_exactly_the_vocabulary() -> None:
    assert set(EVIDENCE_RANK) == set(EVIDENCE_STRENGTHS)
    assert sorted(EVIDENCE_RANK.values()) == list(range(len(EVIDENCE_STRENGTHS)))
    assert len(set(EVIDENCE_STRENGTHS)) == len(EVIDENCE_STRENGTHS)


def test_rank_fails_closed_by_name_rather_than_raising_a_key_error() -> None:
    """`mixed` has no place in a sort key, and a caller must decide, not crash."""

    with pytest.raises(UnknownEvidenceStrength, match="aggregate"):
        rank(MIXED)
    for other in ("", "unknown", "very_lowish", None):
        with pytest.raises(UnknownEvidenceStrength):
            rank(other)  # type: ignore[arg-type]


def test_the_ordinal_map_inverts_the_rank_rather_than_restating_it() -> None:
    for strength, index in EVIDENCE_RANK.items():
        assert strength_for_score(len(EVIDENCE_STRENGTHS) - 1 - index) == strength
    with pytest.raises(UnknownEvidenceStrength):
        strength_for_score(9)


def test_every_strength_has_exactly_one_label() -> None:
    assert set(STRENGTH_LABELS) == set(EVIDENCE_STRENGTHS)
    for strength in EVIDENCE_STRENGTHS:
        assert strength_label(strength) == STRENGTH_LABELS[strength]
    with pytest.raises(UnknownEvidenceStrength):
        strength_label(MIXED)


def test_the_two_thresholds_are_nested_and_the_gap_is_the_context_slice() -> None:
    assert ACTION_THRESHOLD_STRENGTHS < PATIENT_VISIBLE_STRENGTHS
    assert set(EVIDENCE_STRENGTHS) - PATIENT_VISIBLE_STRENGTHS == {"very_low"}
    assert CONTEXT_ONLY_STRENGTHS == PATIENT_VISIBLE_STRENGTHS - ACTION_THRESHOLD_STRENGTHS
    assert {"low"} == CONTEXT_ONLY_STRENGTHS


def test_the_lifecycle_thresholds_are_views_of_the_one_vocabulary() -> None:
    """#221 owns the predicate; #238 owns the values it is evaluated over."""

    assert PATIENT_VISIBLE_GRADES is PATIENT_VISIBLE_STRENGTHS
    assert ACTION_THRESHOLD_GRADES is ACTION_THRESHOLD_STRENGTHS


def test_the_summary_collapses_a_unanimous_finding_and_mixes_a_split_one() -> None:
    assert evidence_strength_summary(["low"]) == "low"
    assert evidence_strength_summary(["moderate", "moderate"]) == "moderate"
    assert evidence_strength_summary(iter(["low", "moderate"])) == MIXED
    assert evidence_strength_summary([]) == MIXED


def test_the_guidance_sentence_agrees_with_the_publish_gate() -> None:
    """The bug this module closes: the sentence said `low` is withheld; the gate
    publishes it as a visible context card."""

    guidance = visibility_guidance()
    assert "极低确定性内容不会发布到患者端" in guidance
    assert "低确定性内容仅作为证据背景卡发布" in guidance
    # The false claim is gone: `low` is not withheld.
    assert "低或极低" not in guidance
    assert "不发布到患者端" not in guidance.replace("极低确定性内容不会发布到患者端", "")


def test_the_vocabulary_payload_carries_the_ui_surface_derived() -> None:
    payload = strength_vocabulary()
    assert payload["values"] == list(EVIDENCE_STRENGTHS)
    assert set(payload["labels"]) == set(EVIDENCE_STRENGTHS)  # type: ignore[arg-type]
    assert payload["patient_visible"] == ["high", "moderate", "low"]
    assert payload["action_threshold"] == ["high", "moderate"]
    assert payload["visibility_guidance"] == visibility_guidance()


@pytest.mark.parametrize(
    ("column", "relative_path"),
    [("certainty", "core/store/schema.py"), ("grade", "core/store/schema.py")],
)
def test_the_sql_check_matches_the_vocabulary(column: str, relative_path: str) -> None:
    """A schema string cannot import Python, so a guard is what keeps it honest."""

    values = _sql_grade_check(relative_path, column)
    assert len(values) == len(EVIDENCE_STRENGTHS), f"{relative_path}: extracted {values}"
    assert values == set(EVIDENCE_STRENGTHS), f"{relative_path} {column} CHECK has drifted"


def test_no_module_restates_the_vocabulary_in_a_literal() -> None:
    """The scan that catches a re-declared vocabulary.

    `Literal[...]` is the shape every restatement took. A span naming three or
    more of the four values is a copy regardless of whether it matches today — a
    copy that has *already* drifted is what a content-equality check cannot see.
    `evidence_strength.py` itself is skipped: its own `CardGrade`/`EvidenceStrength`
    are the derivations, not copies.
    """

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "evidence_strength.py":
            continue
        for match in re.finditer(r"Literal\[([^\]]*)\]", path.read_text()):
            hits = [
                strength
                for strength in EVIDENCE_STRENGTHS
                if re.search(rf"['\"]{re.escape(strength)}['\"]", match.group(1))
            ]
            if len(hits) >= 3:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {sorted(hits)}")
    assert not offenders, "evidence-strength vocabulary restated in a Literal:\n" + "\n".join(
        offenders
    )


def test_the_literal_scan_actually_catches_a_restatement() -> None:
    """A guard that cannot fail is not a guard."""

    def flags(text: str) -> bool:
        for match in re.finditer(r"Literal\[([^\]]*)\]", text):
            hits = [
                strength
                for strength in EVIDENCE_STRENGTHS
                if re.search(rf"['\"]{re.escape(strength)}['\"]", match.group(1))
            ]
            if len(hits) >= 3:
                return True
        return False

    assert flags('Grade = Literal["high", "moderate", "low", "very_low"]')
    assert flags("x: Literal['high', 'moderate', 'low', 'very_low', 'mixed']")
    # The contract aliases are the derivation, not a copy.
    assert not flags("rating: CardGrade")
    # A one- or two-value narrowing is a legitimate shape elsewhere.
    assert not flags('Certainty = Literal["high", "moderate"]')


def test_no_module_restates_the_rank_as_a_dict_literal() -> None:
    """A second `{strength: int}` map is the copy that drifts."""

    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "evidence_strength.py":
            continue
        text = path.read_text()
        for match in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
            span = match.group(0)
            if not re.search(r"['\"]high['\"]\s*:\s*\d", span):
                continue
            hits = [s for s in EVIDENCE_STRENGTHS if f"'{s}'" in span or f'"{s}"' in span]
            if len(hits) > 1:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {sorted(hits)}")
    assert not offenders, "rank map restated as a dict literal:\n" + "\n".join(offenders)


def test_no_module_restates_a_threshold_set_inline() -> None:
    """The two threshold subsets decide publishability, visibility, and the
    patient's action message; a bare set at a call site is that rule re-written.

    The scan matches a set literal whose *membership is exactly* one of the two
    thresholds. That precision is the point: `{'high', 'low'}` is the risk-of-bias
    level vocabulary — a different value class that happens to share two words —
    and a guard that flagged it would be wrong.
    """

    thresholds = (ACTION_THRESHOLD_STRENGTHS, PATIENT_VISIBLE_STRENGTHS)
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name in {"evidence_strength.py", "card_lifecycle.py"}:
            continue
        text = path.read_text()
        for match in re.finditer(r"\{([^{}]*)\}", text):
            values = {item.strip().strip("'\"") for item in match.group(1).split(",")}
            if any(values == set(threshold) for threshold in thresholds):
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}: {sorted(values)}")
    assert not offenders, "threshold set restated inline:\n" + "\n".join(offenders)


def test_the_threshold_scan_actually_catches_a_restatement() -> None:
    def flags(text: str) -> bool:
        thresholds = (ACTION_THRESHOLD_STRENGTHS, PATIENT_VISIBLE_STRENGTHS)
        for match in re.finditer(r"\{([^{}]*)\}", text):
            values = {item.strip().strip("'\"") for item in match.group(1).split(",")}
            if any(values == set(threshold) for threshold in thresholds):
                return True
        return False

    assert flags('if grade in {"high", "moderate"}:')
    assert flags("VISIBLE = {'high', 'moderate', 'low'}")
    # A different value class that shares two words is not this rule.
    assert not flags('RISK = {"high", "low"}')
    assert not flags("THRESHOLDS = ACTION_THRESHOLD_STRENGTHS")
    assert not flags('{"high": 0, "moderate": 1, "low": 2, "very_low": 3}')


def test_the_workbench_renders_its_selector_and_guidance_from_the_vocabulary() -> None:
    """The browser cannot import Python, so it fetches the vocabulary instead.

    The hand-written list and the hand-written sentence are the copies `#238`
    exists to remove; the page must render both from what the API serves.
    """

    source = _source("review/workbench.html")
    assert "/api/review/strength-vocabulary" in source
    assert "${options((strengthVocabulary?.values || []),'moderate')}" in source
    assert "strengthVocabulary?.visibility_guidance" in source
    # The old hand-copied list and sentence are gone from the page.
    assert "options(['high','moderate','low','very_low']" not in source
    assert "低或极低确定性内容不会发布到患者端" not in source


def test_the_dead_coverage_label_is_gone() -> None:
    """`blocked_low_certainty` named a status no branch produces — the ladder emits
    `blocked_very_low_certainty` and `published_context` only."""

    source = _source("review/workbench.html")
    assert "blocked_low_certainty" not in source
    assert "blocked_very_low_certainty" in source


def test_the_review_api_serves_the_vocabulary_the_workbench_reads() -> None:
    source = _source("review/api.py")
    assert "/api/review/strength-vocabulary" in source
    assert "evidence_strength.strength_vocabulary()" in source
