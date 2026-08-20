"""Persistent single-process worker for long-running paper extraction."""

from __future__ import annotations

import fcntl
import logging
import os
import time
from pathlib import Path
from typing import Protocol

from ..core.store import Database, ObjectStore, PaperStore, ReviewStore
from ..review.service import EvidenceReviewService
from .ai_extraction import (
    ArkPaperAnalyzer,
    CheckedPaperExtraction,
    ConsistencyReport,
    PaperExtraction,
)
from .jats import JatsParser
from .models import PaperRecord


class StageAnalyzer(Protocol):
    @property
    def model(self) -> str: ...

    def extract(
        self, paper: PaperRecord, document: dict[str, object]
    ) -> tuple[PaperExtraction, str]: ...

    def check(
        self,
        paper: PaperRecord,
        document: dict[str, object],
        extraction: PaperExtraction,
        second_extraction: PaperExtraction,
    ) -> tuple[ConsistencyReport, str]: ...


class PaperAutoReviewer(Protocol):
    def auto_review_paper(self, paper_id: str, *, requested_by: str) -> dict[str, object]: ...


class LiteratureExtractionWorker:
    def __init__(
        self,
        *,
        store: PaperStore,
        objects: ObjectStore,
        analyzer: StageAnalyzer,
        auto_reviewer: PaperAutoReviewer | None = None,
        requested_by: str = "ai:extraction-worker",
    ) -> None:
        self._store = store
        self._objects = objects
        self._analyzer = analyzer
        self._auto_reviewer = auto_reviewer
        self._requested_by = requested_by
        self._jats = JatsParser()

    def run_once(self) -> str | None:
        job = self._store.claim_next_extraction_job()
        if job is None:
            return None
        job_id = str(job["id"])
        try:
            paper, object_key = self._store.get_analysis_source(str(job["paper_id"]))
            document = self._jats.parse(self._objects.read(object_key)).to_dict()
            extraction = self._extraction_a(job_id, job, paper, document)
            second_extraction = self._extraction_b(job_id, job, paper, document)
            consistency = self._consistency(
                job_id,
                job,
                paper,
                document,
                extraction,
                second_extraction,
            )
            current = self._store.get_extraction_job(job_id)
            if current is None:
                raise ValueError("extraction job disappeared")
            self._store.save_ai_extraction(
                str(job["paper_id"]),
                CheckedPaperExtraction(
                    model=str(current["model"]),
                    extraction_run_id=str(current["extraction_run_id"]),
                    extraction=extraction,
                    second_model=str(current["second_model"]),
                    second_run_id=str(current["second_run_id"]),
                    second_extraction=second_extraction,
                    check_model=str(current["check_model"]),
                    check_run_id=str(current["check_run_id"]),
                    consistency=consistency,
                ),
            )
            self._store.complete_extraction_job(job_id)
        except Exception as exc:
            self._store.fail_extraction_job(job_id, exc)
        else:
            self._auto_review(str(job["paper_id"]), job_id)
        return job_id

    def _auto_review(self, paper_id: str, job_id: str) -> None:
        if self._auto_reviewer is None:
            return
        try:
            self._auto_reviewer.auto_review_paper(paper_id, requested_by=self._requested_by)
        except Exception as exc:
            try:
                self._store.record_event(
                    "paper",
                    paper_id,
                    "autonomous_review_failed",
                    {
                        "job_id": job_id,
                        "requested_by": self._requested_by,
                        "error_class": type(exc).__name__,
                        "error_message": str(exc),
                    },
                    actor="ai:extraction-worker",
                )
            except Exception:
                logging.exception("failed to audit autonomous review failure for %s", paper_id)

    def _extraction_a(
        self,
        job_id: str,
        job: dict[str, object],
        paper: PaperRecord,
        document: dict[str, object],
    ) -> PaperExtraction:
        if job["extraction_json"]:
            return PaperExtraction.model_validate_json(str(job["extraction_json"]))
        extraction, run_id = self._analyzer.extract(paper, document)
        self._store.save_extraction_channel(
            job_id,
            channel="a",
            model=self._analyzer.model,
            run_id=run_id,
            extraction_json=extraction.model_dump_json(),
        )
        return extraction

    def _extraction_b(
        self,
        job_id: str,
        job: dict[str, object],
        paper: PaperRecord,
        document: dict[str, object],
    ) -> PaperExtraction:
        if job["second_extraction_json"]:
            return PaperExtraction.model_validate_json(str(job["second_extraction_json"]))
        extraction, run_id = self._analyzer.extract(paper, document)
        self._store.save_extraction_channel(
            job_id,
            channel="b",
            model=self._analyzer.model,
            run_id=run_id,
            extraction_json=extraction.model_dump_json(),
        )
        return extraction

    def _consistency(
        self,
        job_id: str,
        job: dict[str, object],
        paper: PaperRecord,
        document: dict[str, object],
        extraction: PaperExtraction,
        second_extraction: PaperExtraction,
    ) -> ConsistencyReport:
        if job["consistency_json"]:
            return ConsistencyReport.model_validate_json(str(job["consistency_json"]))
        consistency, run_id = self._analyzer.check(
            paper,
            document,
            extraction,
            second_extraction,
        )
        self._store.save_extraction_consistency(
            job_id,
            model=self._analyzer.model,
            run_id=run_id,
            consistency_json=consistency.model_dump_json(),
        )
        return consistency


def main() -> None:
    analyzer = ArkPaperAnalyzer.from_env()
    if not analyzer.api_key_configured:
        raise SystemExit(
            "PAPER_AI_API_KEY_FILE/PAPER_AI_API_KEY (or legacy ARK_API_KEY) is not configured; "
            "the extraction worker cannot start"
        )
    database_path = Path(os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3"))
    database = Database(database_path)
    database.initialize()
    store = PaperStore(database)
    reviewer_id = os.getenv("GENESIS_EVIDENCE_REVIEWER_ID", "").strip()
    if not reviewer_id:
        raise SystemExit(
            "GENESIS_EVIDENCE_REVIEWER_ID is not configured; "
            "the extraction worker cannot audit autonomous review requests"
        )
    worker = LiteratureExtractionWorker(
        store=store,
        objects=ObjectStore(os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects")),
        analyzer=analyzer,
        auto_reviewer=EvidenceReviewService(ReviewStore(database), store),
        requested_by=reviewer_id,
    )
    lock_path = database_path.with_suffix(f"{database_path.suffix}.extraction-worker.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store.recover_running_extraction_jobs()
        poll_seconds = max(1.0, float(os.getenv("GENESIS_EVIDENCE_WORKER_POLL_SECONDS", "5")))
        while True:
            if worker.run_once() is None:
                time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
