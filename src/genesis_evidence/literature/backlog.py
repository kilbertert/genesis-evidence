"""Process the persisted full-text and extraction backlog."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from ..core.store import Database, ObjectStore, PaperStore, ReviewStore
from ..review.service import EvidenceReviewService
from .downloader import FullTextDownloader
from .http import HttpClient
from .ingestion import LiteratureIngestionService
from .integrity import (
    CrossrefIntegrityProvider,
    EuropePmcIntegrityProvider,
    PublicationIntegrityChecker,
)
from .policy import SourcePolicyRegistry


def main() -> None:
    database_path = os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3")
    database = Database(Path(database_path))
    database.initialize()
    store = PaperStore(database)
    policy = SourcePolicyRegistry()
    topic_id = (
        os.getenv("GENESIS_EVIDENCE_ACTIVE_TOPIC_ID")
        or os.getenv("GENESIS_EVIDENCE_BACKLOG_TOPIC_ID")
        or None
    )

    # Screen before retrieving. `retrieve_pending_full_texts` selects
    # `title_abstract_decision = 'included'`, so a paper nobody has screened is also a
    # paper nobody fetches -- and the extraction worker only claims `queued` jobs, which
    # an unscreened paper does not have. ADR 0015 made screening *possible* without a
    # full text; this is the driver that makes it *happen*.
    review_objects = ObjectStore(os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects"))
    reviewer = EvidenceReviewService(ReviewStore(database), store, review_objects)
    screened = 0
    for paper_id in store.list_unscreened_papers(topic_id=topic_id):
        try:
            reviewer.auto_review_paper(paper_id, requested_by="ai:screening-ledger")
        except Exception as exc:  # noqa: BLE001 - one paper must not stop the run
            store.record_event(
                "paper",
                paper_id,
                "backlog_screening_failed",
                {"error_class": type(exc).__name__, "error_message": str(exc)[:300]},
                actor="ai:screening-ledger",
            )
            continue
        screened += 1

    with HttpClient(user_agent="GenesisEvidence/0.1 evidence-backlog") as http:
        service = LiteratureIngestionService(
            store=store,
            objects=ObjectStore(os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects")),
            downloader=FullTextDownloader(
                http,
                policy,
                max_download_bytes=int(
                    os.getenv("GENESIS_EVIDENCE_FULL_TEXT_MAX_BYTES", 25 * 1024 * 1024)
                ),
            ),
            integrity=PublicationIntegrityChecker(
                (EuropePmcIntegrityProvider(http), CrossrefIntegrityProvider(http))
            ),
        )
        summary = service.retrieve_pending_full_texts(topic_id=topic_id)
    superseded = store.supersede_excluded_extraction_failures(
        reviewer="ai:retrieval-worker", topic_id=topic_id
    )
    restored = store.restore_screening_excluded_extractions(
        reviewer="ai:retrieval-worker",
        topic_id=topic_id,
    )
    retried = 0
    for job in store.list_extraction_jobs(limit=500, topic_id=topic_id, latest_per_paper=True):
        if job["status"] == "failed":
            store.retry_extraction(str(job["id"]), reviewer="ai:extraction-worker")
            retried += 1
    print(
        json.dumps(
            {
                **asdict(summary),
                "screened": screened,
                "superseded_failures": superseded,
                "restored_screening_exclusions": restored,
                "retried_failures": retried,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
