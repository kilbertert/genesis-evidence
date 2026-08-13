"""Persistent single-process worker for health report extraction."""

from __future__ import annotations

import asyncio
import fcntl
import os
import time
from pathlib import Path

from ..core.store import Database, ObjectStore, ReportStore
from .extraction import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_REPORT_MODEL,
    HealthReportExtractor,
)


class ReportExtractionWorker:
    def __init__(self, *, store: ReportStore, extractor: HealthReportExtractor) -> None:
        self._store = store
        self._extractor = extractor

    def run_once(self) -> str | None:
        report_id = self._store.claim_next_extraction()
        if report_id is None:
            return None
        try:
            files = self._store.load_files(report_id)
            extracted = asyncio.run(self._extractor.extract_files(files))
            self._store.save_extraction(report_id, extracted)
        except Exception as exc:
            self._store.fail_extraction(report_id, exc)
        return report_id


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not configured; the report worker cannot start")
    database_path = Path(os.getenv("GENESIS_EVIDENCE_DATABASE", "var/genesis-evidence.sqlite3"))
    database = Database(database_path)
    database.initialize()
    store = ReportStore(
        database,
        ObjectStore(os.getenv("GENESIS_EVIDENCE_OBJECTS", "var/objects")),
    )
    worker = ReportExtractionWorker(
        store=store,
        extractor=HealthReportExtractor(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
            responses_url=os.getenv("OPENAI_RESPONSES_URL", ""),
            model=os.getenv("OPENAI_REPORT_MODEL", DEFAULT_REPORT_MODEL),
            max_bytes=int(os.getenv("GENESIS_EVIDENCE_REPORT_MAX_BYTES", 20 * 1024 * 1024)),
        ),
    )
    lock_path = database_path.with_suffix(f"{database_path.suffix}.report-worker.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store.recover_running_extractions()
        poll_seconds = max(1.0, float(os.getenv("GENESIS_EVIDENCE_REPORT_POLL_SECONDS", "2")))
        while True:
            if worker.run_once() is None:
                time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
