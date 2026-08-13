"""Persistent single-process worker for long-running paper extraction."""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path
from typing import Protocol

from ..core.store import Database, ObjectStore, PaperStore
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


class LiteratureExtractionWorker:
    def __init__(
        self,
        *,
        store: PaperStore,
        objects: ObjectStore,
        analyzer: StageAnalyzer,
    ) -> None:
        self._store = store
        self._objects = objects
        self._analyzer = analyzer
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
        return job_id

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
    database_path = Path(os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3"))
    database = Database(database_path)
    database.initialize()
    store = PaperStore(database)
    worker = LiteratureExtractionWorker(
        store=store,
        objects=ObjectStore(os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects")),
        analyzer=ArkPaperAnalyzer.from_env(),
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
