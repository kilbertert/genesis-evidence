"""The queue projection and the workbench detail must agree on a paper's review state.

They are separate implementations — `list_review_queue` derives `review_state` in SQL, and
`_review_guidance` derives `state` in Python — so nothing but this gate keeps them aligned.
A paper that reads one way in the queue and another in `当前审核步骤` is the drift the
architecture review flagged (#137); this pins the agreement rather than reproducing the
whole decision tree in a third place.
"""

from __future__ import annotations

import uuid

from genesis_evidence.core.store import Database, PaperStore, ReviewStore
from genesis_evidence.review.service import EvidenceReviewService

from .test_review_workflow import _complete_topic, _review_case
from .test_topic_governance import _topic


def _states(database: Database, paper_id: str) -> tuple[str, str, str | None]:
    store = ReviewStore(database)
    queue = {str(row["id"]): row for row in store.list_review_queue()}
    item = store.get_review_item(paper_id)
    assert item is not None
    guidance = item["review_guidance"]
    return (
        str(queue[paper_id]["review_state"]),
        str(guidance["state"]),
        guidance.get("terminal_decision"),
    )


def _assert_parity(database: Database, paper_id: str) -> None:
    queued, detailed, _ = _states(database, paper_id)
    assert queued == detailed, (
        f"queue says {queued!r} while 当前审核步骤 says {detailed!r} for {paper_id}"
    )


def test_parity_after_a_fresh_review_case(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_id, _ = _review_case(database)

    _assert_parity(database, paper_id)


def test_parity_once_the_topic_is_complete(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_id, _ = _review_case(database)
    _complete_topic(database, paper_id)

    _assert_parity(database, paper_id)


def test_parity_when_retrieval_closes_the_ledger(tmp_path) -> None:
    """The one terminal path the workbench does expose: full text never obtained."""

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    included, _ = _review_case(database)
    missing = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, created_at) VALUES (?, 'Unavailable', 'now')",
            (missing,),
        )
        connection.execute(
            "INSERT INTO paper_sources(paper_id, source, source_id, source_url) "
            "VALUES (?, 'test', ?, 'https://example.test/unavailable')",
            (missing, missing),
        )
        connection.execute(
            "INSERT INTO paper_admissions(paper_id, status) VALUES (?, 'pending')", (missing,)
        )
    topic_id = _complete_topic(database, included)
    store = PaperStore(database)
    run_id = store.start_collection(topic_id=topic_id, source="test", query="missing")
    store.add_to_collection(run_id, missing, position=1)
    store.finish_collection(run_id, status="completed", detail={})
    store.screen_collection_paper(
        run_id,
        missing,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="reviewer-1",
    )
    store.record_full_text_retrieval(
        run_id,
        missing,
        status="not_retrieved",
        reason="Repository exposes metadata but no legally retrievable full text.",
        reviewer="reviewer-1",
    )

    _assert_parity(database, missing)
    queued, detailed, terminal = _states(database, missing)
    assert (queued, detailed, terminal) == ("completed", "completed", "not_retrieved")


def test_parity_when_a_screening_exclusion_rejects_the_paper(tmp_path) -> None:
    """Terminal at screening: a paper excluded on title/abstract, never retrieved."""

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    paper_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, publication_status, integrity_status, created_at) "
            "VALUES (?, 'Excluded at screening', 'formal', 'clear', 'now')",
            (paper_id,),
        )
        connection.execute(
            "INSERT INTO paper_sources(paper_id, source, source_id, source_url) "
            "VALUES (?, 'test', ?, 'https://example.test/excluded')",
            (paper_id, paper_id),
        )
    run_id = store.start_collection(topic_id=topic_id, source="test", query="excluded")
    store.add_to_collection(run_id, paper_id, position=1)
    store.finish_collection(run_id, status="completed", detail={})
    store.screen_collection_paper(
        run_id,
        paper_id,
        stage="title_abstract",
        decision="excluded",
        exclusion_reason="wrong_population",
        reviewer="reviewer-1",
    )

    _assert_parity(database, paper_id)

    result = EvidenceReviewService(ReviewStore(database), store).auto_review_paper(
        paper_id, requested_by="authenticated-reviewer"
    )
    assert result["decision"] == "excluded"

    _assert_parity(database, paper_id)


def test_parity_after_a_named_reviewer_rejects_the_paper(tmp_path) -> None:
    """`reject_paper` is a second route to a terminal rejection, with no screening record."""

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_id, _ = _review_case(database)
    service = EvidenceReviewService(ReviewStore(database), PaperStore(database))
    service.reject_paper(paper_id, reviewer="reviewer-1")

    _assert_parity(database, paper_id)
    _, detailed, _ = _states(database, paper_id)
    assert detailed == "blocked"


def test_parity_after_autonomous_review_completes_the_flow(tmp_path) -> None:
    """The `completed` state reached through the normal admission and claim path."""

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_id, _ = _review_case(database)
    _complete_topic(database, paper_id)
    service = EvidenceReviewService(ReviewStore(database), PaperStore(database))
    service.auto_review_paper(paper_id, requested_by="authenticated-reviewer")

    _assert_parity(database, paper_id)
