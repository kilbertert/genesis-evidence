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
    # `_review_case` already completes a topic, so this is the post-completion state.
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_id, _ = _review_case(database)

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


def test_parity_for_a_terminal_screening_exclusion(tmp_path) -> None:
    """A paper excluded at screening and closed by the reviewer reads `completed` in both.

    Before #210 it read `blocked` with `terminal_decision=None` forever: the reviewer acted
    on the exclusion but neither projection showed the paper was finished. The queue CASE
    and the guidance branch now share one predicate.
    """

    import uuid

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
    queued, detailed, terminal = _states(database, paper_id)
    assert (queued, detailed, terminal) == ("completed", "completed", "excluded")

    result = EvidenceReviewService(ReviewStore(database), store).auto_review_paper(
        paper_id, requested_by="authenticated-reviewer"
    )
    assert result["decision"] == "excluded"
    # Still terminal, still agreeing, after the reviewer acted.
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


def test_parity_for_an_extraction_without_claims(tmp_path) -> None:
    """A claimless extraction is `blocked` in both projections (#211).

    Before, the queue's CASE fell through to `ready_for_automation` while the detail said
    `blocked` and `auto_review_paper` answered `attention_required` — the queue advertised
    work that the detail knew could not proceed. The queue now applies the same
    structured-results check the detail does.
    """

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_id, _ = _review_case(database)
    with database.transaction() as connection:
        connection.execute("DELETE FROM claims WHERE paper_id = ?", (paper_id,))

    _assert_parity(database, paper_id)
    queued, detailed, _ = _states(database, paper_id)
    assert (queued, detailed) == ("blocked", "blocked")


def test_parity_for_an_incomplete_structured_result(tmp_path) -> None:
    """An empty required field blocks both projections, not just the detail."""

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_id, claim_id = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE results SET population = '' WHERE id = "
            "(SELECT result_id FROM claims WHERE id = ?)",
            (claim_id,),
        )

    _assert_parity(database, paper_id)
    queued, detailed, _ = _states(database, paper_id)
    assert (queued, detailed) == ("blocked", "blocked")


def test_an_open_collection_keeps_a_paper_non_terminal(tmp_path) -> None:
    """A run still in progress means the paper is not finished, in both projections.

    Before this, the queue's exclusion branch looked only at completed locked runs, so a
    paper with one excluded completed run and one undecided running run read `completed`
    while the detail stayed `blocked`.
    """

    import uuid

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    paper_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, publication_status, integrity_status, created_at) "
            "VALUES (?, 'Open collection', 'formal', 'clear', 'now')",
            (paper_id,),
        )
        connection.execute(
            "INSERT INTO paper_sources(paper_id, source, source_id, source_url) "
            "VALUES (?, 'test', ?, 'https://example.test/open')",
            (paper_id, paper_id),
        )
    excluded_run = store.start_collection(topic_id=topic_id, source="test", query="done")
    store.add_to_collection(excluded_run, paper_id, position=1)
    store.finish_collection(excluded_run, status="completed", detail={})
    store.screen_collection_paper(
        excluded_run,
        paper_id,
        stage="title_abstract",
        decision="excluded",
        exclusion_reason="wrong_population",
        reviewer="reviewer-1",
    )
    # A second run that has not finished: it can still reach a different conclusion.
    open_run = store.start_collection(topic_id=topic_id, source="test", query="open")
    store.add_to_collection(open_run, paper_id, position=1)

    queued, detailed, terminal = _states(database, paper_id)
    assert (queued, detailed, terminal) == ("blocked", "blocked", None)


def test_exclusion_outranks_not_retrieved_for_a_mixed_paper(tmp_path) -> None:
    """A screening exclusion is the paper's terminal decision, even beside a not-retrieved run.

    The retrieval branch used to run first and permit other collections to be title-excluded,
    so a paper with both a not-retrieved run and an excluded run reported `not_retrieved`
    while `auto_review_paper` rejected it as `excluded` — two terminal decisions for one
    paper.
    """

    import uuid

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _topic(store)
    paper_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO papers(id, title, publication_status, integrity_status, created_at) "
            "VALUES (?, 'Mixed terminal', 'formal', 'clear', 'now')",
            (paper_id,),
        )
        connection.execute(
            "INSERT INTO paper_sources(paper_id, source, source_id, source_url) "
            "VALUES (?, 'test', ?, 'https://example.test/mixed')",
            (paper_id, paper_id),
        )
    not_retrieved_run = store.start_collection(topic_id=topic_id, source="test", query="nr")
    store.add_to_collection(not_retrieved_run, paper_id, position=1)
    store.finish_collection(not_retrieved_run, status="completed", detail={})
    store.screen_collection_paper(
        not_retrieved_run,
        paper_id,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="reviewer-1",
    )
    store.record_full_text_retrieval(
        not_retrieved_run,
        paper_id,
        status="not_retrieved",
        reason="no legally retrievable copy",
        reviewer="reviewer-1",
    )
    excluded_run = store.start_collection(topic_id=topic_id, source="test", query="ex")
    store.add_to_collection(excluded_run, paper_id, position=1)
    store.finish_collection(excluded_run, status="completed", detail={})
    store.screen_collection_paper(
        excluded_run,
        paper_id,
        stage="title_abstract",
        decision="excluded",
        exclusion_reason="wrong_population",
        reviewer="reviewer-1",
    )

    _, detailed, terminal = _states(database, paper_id)
    assert terminal == "excluded"

    result = EvidenceReviewService(ReviewStore(database), store).auto_review_paper(
        paper_id, requested_by="authenticated-reviewer"
    )
    assert result["decision"] == "excluded", "the reviewer and the ledger must name one decision"
    _assert_parity(database, paper_id)


def test_parity_for_an_admitted_paper_whose_claims_are_gone(tmp_path) -> None:
    """Admission does not outrank the structured-results check.

    The admission branch returned `completed` for an `internally_admitted` paper before the
    structured-results gate was consulted, so a paper admitted earlier and now carrying no
    claims read done in the queue while the detail reported it blocked.
    """

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    paper_id, _ = _review_case(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE paper_admissions SET status = 'internally_admitted' WHERE paper_id = ?",
            (paper_id,),
        )
        connection.execute("DELETE FROM claims WHERE paper_id = ?", (paper_id,))

    _assert_parity(database, paper_id)
    queued, detailed, _ = _states(database, paper_id)
    assert (queued, detailed) == ("blocked", "blocked")
