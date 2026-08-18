"""Process the persisted full-text and extraction backlog."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from ..core.store import Database, ObjectStore, PaperStore
from .downloader import FullTextDownloader
from .http import HttpClient
from .ingestion import LiteratureIngestionService
from .integrity import PublicationIntegrityChecker
from .policy import SourcePolicyRegistry


def main() -> None:
    database_path = os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3")
    database = Database(Path(database_path))
    database.initialize()
    store = PaperStore(database)
    policy = SourcePolicyRegistry()
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
            integrity=PublicationIntegrityChecker(()),
        )
        summary = service.retrieve_pending_full_texts()
    superseded = store.supersede_excluded_extraction_failures(
        reviewer="ai:retrieval-worker"
    )
    retried = 0
    for job in store.list_extraction_jobs(limit=500):
        if job["status"] == "failed":
            store.retry_extraction(str(job["id"]), reviewer="ai:extraction-worker")
            retried += 1
    print(
        json.dumps(
            {
                **asdict(summary),
                "superseded_failures": superseded,
                "retried_failures": retried,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
