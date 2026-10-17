"""Adjudication provenance has one owner, and it must not drift from what it replaced.

Only the resolution *text* is persisted, so provenance is inferred on every read. The
inference is the thing that decides whether the autonomous reviewer may clear the `dual_ai`
blocker without a human signature, so it gets one home and one truth table.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_evidence.core.consistency import (
    AUTOMATIC_ACTOR_PREFIX,
    AUTOMATIC_RESOLUTION_PREFIX,
    AUTONOMOUS_REOPENABLE_PREFIXES,
    CONSISTENT,
    MACHINE_ACTOR_PREFIXES,
    NEEDS_REVIEW,
    SOURCE_ADJUDICATED_ACTOR_PREFIX,
    adjudication_source,
    automatic_actor_for_model,
    is_automatic,
    is_autonomous_reopenable,
    is_machine_actor,
    is_source_based,
    source_adjudicated_actor_for_model,
)


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        (None, "none"),
        ("", "none"),
        ("   ", "none"),
        (AUTOMATIC_RESOLUTION_PREFIX + "v1): primary remains the source of record.", "automatic"),
        ("Source-based AI adjudication (v1): every difference is grounded.", "source_based"),
        ("人工裁决：采用 A 通道结论。", "source_based"),
    ],
)
def test_provenance_classification(resolution: object, expected: str) -> None:
    assert adjudication_source(resolution) == expected


def test_the_two_predicates_read_the_same_classification() -> None:
    automatic = AUTOMATIC_RESOLUTION_PREFIX + "v1): automatic."
    source = "Source-based AI adjudication (v1): grounded."
    assert is_automatic(automatic) and not is_source_based(automatic)
    assert is_source_based(source) and not is_automatic(source)
    assert not is_source_based(None) and not is_automatic(None)


def test_the_verdict_vocabulary_is_the_stored_one() -> None:
    # These strings are the `consistency_status` CHECK values; a rename must break here first.
    assert (CONSISTENT, NEEDS_REVIEW) == ("consistent", "needs_review")


def test_provenance_is_inferred_from_text_and_that_is_a_known_limit() -> None:
    """A human override opening with the automatic prefix reads as automatic.

    The ticket that commissioned this module claimed the opposite direction — that rewording
    the *producer* would make adjudications read as source-based and clear the blocker. It is
    the other way round, and it is reachable through the human path: the workbench textarea
    posts free text to `admit_paper`, and only that text is stored. Pinned so the limit is
    visible rather than assumed away.
    """

    human_but_indistinguishable = AUTOMATIC_RESOLUTION_PREFIX + "legacy): 人工确认 A 通道。"

    assert adjudication_source(human_but_indistinguishable) == "automatic"


def test_the_prefix_matches_the_producer_it_names() -> None:
    """The constant must be the literal the producer writes, or classification silently flips."""

    from pathlib import Path

    service = (
        Path(__file__).resolve().parents[1] / "src" / "genesis_evidence" / "review" / "service.py"
    ).read_text()

    assert AUTOMATIC_RESOLUTION_PREFIX in service, (
        "the automatic-resolution producer no longer writes this prefix; provenance would "
        "flip for every stored adjudication"
    )


def test_every_declared_name_has_a_production_consumer() -> None:
    """A constant nothing reads is a claim of ownership the code does not honour.

    The first version of this module declared `CONSISTENT`/`NEEDS_REVIEW` while every
    production site still spelled the literal, so a rename would have changed nothing.
    """

    root = Path(__file__).resolve().parents[1] / "src" / "genesis_evidence"
    sources = "\n".join(
        path.read_text() for path in root.rglob("*.py") if path.name != "consistency.py"
    )
    # The *name*, in Python — not the value. Matching the value is satisfied by the schema's
    # copy of the literal, which is exactly how an orphaned constant slipped through before.
    #
    # The exported verdict vocabulary only. `AUTOMATIC_RESOLUTION_PREFIX` is an internal
    # needle this module's own predicate reads; demanding an external consumer for it would
    # be asking for a pointless one.
    for name in ("CONSISTENT", "NEEDS_REVIEW"):
        assert name in sources, f"{name} is declared but no Python module consumes it"


def test_no_production_module_spells_the_verdict_inline() -> None:
    """The verdict literals live in the owner; a copy is the drift this module prevents."""

    root = Path(__file__).resolve().parents[1] / "src" / "genesis_evidence"
    allowed = {"core/consistency.py", "core/store/schema.py"}
    for path in root.rglob("*.py"):
        relative = str(path.relative_to(root))
        if relative in allowed:
            continue
        source = path.read_text()
        assert '"needs_review"' not in source, f"{relative} spells the verdict inline"
        assert '"consistent"' not in source, f"{relative} spells the verdict inline"


# --- Actor provenance: the second evidence channel for the same question ---


def test_the_actor_prefix_matches_the_producers_that_write_it() -> None:
    """The prefix list must name the literals the producers write, or classification flips.

    The direct analogue of `test_the_prefix_matches_the_producer_it_names` above, and the
    guard whose absence let the defect through: `review/service.py` wrote `source-ai:` while
    all five readers tested `ai:`, so a machine's claim rejection was stored as a person's
    decision and no reader would ever reopen it (#311).
    """

    service = (
        Path(__file__).resolve().parents[1] / "src" / "genesis_evidence" / "review" / "service.py"
    ).read_text()

    # The producers go through the constructors, so the prefix must not be assembled by hand
    # anywhere — that is the shape that drifted.
    assert 'f"ai:' not in service and 'f"source-ai:' not in service, (
        "a producer assembles a machine prefix by hand; actor provenance would flip for every "
        "claim decision written that way"
    )
    for name in ("automatic_actor_for_model", "source_adjudicated_actor_for_model"):
        assert name in service, f"{name} is declared but the producer does not use it"


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("ai:checker", True),
        ("ai:", True),
        # The one the five readers missed. It is a machine decision — written by the
        # autonomous pipeline's source-adjudication branch — that the old prefix test read
        # as a person's.
        ("source-ai:checker", True),
        # `system:` values reach only `full_text_retrieval_reviewer`, which no reader
        # tests, so the prefix is deliberately not in the set.
        ("system:ingestion", False),
        # A person's free-form name, and the cases that must NOT be read as machines.
        ("ranlei", False),
        ("reviewer-1", False),
        # Unattributed is not machine: an absent row is not evidence of an autonomous
        # decision, and the readmission gate at service.py:281 is its consumer.
        (None, False),
        ("", False),
        ("   ", False),
    ],
)
def test_machine_actor_classification(actor: object, expected: bool) -> None:
    assert is_machine_actor(actor) is expected


def test_the_actor_constructors_round_trip_through_the_predicate() -> None:
    """A constructor's output must be classified by the predicate the readers use.

    This is the invariant that was broken: the string `review/service.py` wrote and the
    string its readers tested had to agree, and nothing asserted that.
    """

    for model in ("checker", None, ""):
        assert is_machine_actor(automatic_actor_for_model(model))
        assert is_machine_actor(source_adjudicated_actor_for_model(model))
    assert automatic_actor_for_model("checker") == "ai:checker"
    assert source_adjudicated_actor_for_model("checker") == "source-ai:checker"


def test_the_source_channel_and_the_actor_channel_can_disagree() -> None:
    """Two evidence channels answer one question, and the divergence is the point.

    The source-adjudication branch writes a machine actor (`source-ai:`) *and* a resolution
    sentence that classifies `source_based` — human-ish. Keeping the two classifications
    separate is deliberate: the actor channel says a machine acted, the text channel says the
    adjudication rested on the full text. Collapsing them would lose the audit trail this
    module exists to keep, so the disagreement is pinned rather than resolved.
    """

    machine = source_adjudicated_actor_for_model("checker")
    assert is_machine_actor(machine)
    assert is_source_based("Source-based AI adjudication (v1): every difference is grounded.")
    assert not is_automatic("Source-based AI adjudication (v1): every difference is grounded.")


def test_the_declared_actor_prefixes_are_the_ones_the_readers_read() -> None:
    """No module may test a machine prefix that is not in the declared set."""

    root = Path(__file__).resolve().parents[1] / "src" / "genesis_evidence"
    for path in root.rglob("*.py"):
        if path.name == "consistency.py":
            continue
        source = path.read_text()
        for prefix in ("ai:", "source-ai:", "system:"):
            assert f'startswith("{prefix}")' not in source, (
                f"{path.relative_to(root)} hand-spells the machine prefix {prefix!r}; "
                "is_machine_actor owns that test"
            )
    assert MACHINE_ACTOR_PREFIXES == (
        AUTOMATIC_ACTOR_PREFIX,
        SOURCE_ADJUDICATED_ACTOR_PREFIX,
    )


def test_the_reopen_predicate_is_strictly_narrower_than_the_classifier() -> None:
    """`is_machine_actor` says who acted; `is_autonomous_reopenable` says what may follow.

    They must not be conflated in either direction. Widening the reopen predicate to every
    machine actor lets a later pass overturn a source-adjudicated rejection it can no longer
    re-derive; narrowing the classifier to just `ai:` restores the #311 defect, where a
    machine's decision reads as a person's.
    """

    assert set(AUTONOMOUS_REOPENABLE_PREFIXES) < set(MACHINE_ACTOR_PREFIXES)
    # The source-adjudicated prefix is a machine and is NOT reopenable — the whole point.
    assert is_machine_actor("source-ai:checker")
    assert not is_autonomous_reopenable("source-ai:checker")
    # The pipeline's own signature is both.
    assert is_machine_actor("ai:checker") and is_autonomous_reopenable("ai:checker")
    # A person's name is neither.
    assert not is_machine_actor("ranlei") and not is_autonomous_reopenable("ranlei")
    # Unattributed is neither: an absent row is not an autonomous decision.
    for blank in (None, "", "   "):
        assert not is_machine_actor(blank) and not is_autonomous_reopenable(blank)


def test_the_reopen_predicate_is_what_the_claim_gates_actually_call() -> None:
    """The gates must ask the reopen question, not merely the classification question.

    Reading the two claim-reopen sites as `is_machine_actor` was a real defect introduced and
    caught while fixing #311: it silently turned a `source-ai:` rejection into `approved` on
    the second pass, on a run that re-verified nothing.
    """

    service = (
        Path(__file__).resolve().parents[1] / "src" / "genesis_evidence" / "review" / "service.py"
    ).read_text()
    # Two claim-reopen gates, both on the narrower predicate.
    assert service.count("is_autonomous_reopenable(claim.get(\"reviewer\"))") == 2


def test_the_actor_scan_discriminates() -> None:
    """The scan above must fail on a hand-spelled prefix, or it is not a guard."""

    def flags(text: str) -> bool:
        return any(f'startswith("{p}")' in text for p in ("ai:", "source-ai:", "system:"))

    assert flags('and str(x or "").startswith("ai:")')
    assert flags('str(admission.get("reviewer") or "").startswith("ai:")')
    assert not flags("and is_machine_actor(x)")
