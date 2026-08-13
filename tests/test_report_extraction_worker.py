from __future__ import annotations

from genesis_evidence.core.store import Database, ObjectStore, ReportStore
from genesis_evidence.reports.extraction import (
    HealthReportExtractor,
    ModelObservation,
    ModelReportExtraction,
    ReportFile,
    ReportProviderResult,
)
from genesis_evidence.reports.extraction_worker import ReportExtractionWorker


class Provider:
    async def understand(self, files):
        return ReportProviderResult(
            provider="fake",
            model="fake-model",
            run_id="run-1",
            extraction=ModelReportExtraction(
                subject_consistency="same",
                inferred_age=None,
                inferred_sex="unknown",
                observations=[
                    ModelObservation(
                        source_file_index=1,
                        source_page=1,
                        name="空腹血糖",
                        value=6.8,
                        unit="mmol/L",
                        reference_low=3.9,
                        reference_high=6.1,
                        flag="high",
                        evidence="空腹血糖 6.8 mmol/L 3.9-6.1 H",
                        extraction_status="clear",
                    )
                ],
                abnormality_audit=[],
            ),
        )


def test_worker_processes_persisted_report(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = ReportStore(database, ObjectStore(tmp_path / "objects"))
    handle = store.create((ReportFile(b"report", "report.txt", "text/plain"),))
    worker = ReportExtractionWorker(
        store=store,
        extractor=HealthReportExtractor(max_bytes=1024, provider=Provider()),
    )

    assert worker.run_once() == handle.report_id
    result = store.get(handle.report_id, handle.access_token)
    assert result["status"] == "pending_confirmation"
    assert result["observations"][0]["model_value"] == 6.8
    assert worker.run_once() is None
