"""Adjudication provenance has one owner, and it must not drift from what it replaced.

Only the resolution *text* is persisted, so provenance is inferred on every read. The
inference is the thing that decides whether the autonomous reviewer may clear the `dual_ai`
blocker without a human signature, so it gets one home and one truth table.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_evidence.core.consistency import (
    AUTOMATIC_RESOLUTION_PREFIX,
    CONSISTENT,
    NEEDS_REVIEW,
    adjudication_source,
    is_automatic,
    is_source_based,
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
