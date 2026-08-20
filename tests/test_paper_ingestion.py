from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from genesis_evidence.core.store import Database, ObjectStore, PaperStore
from genesis_evidence.literature.ai_extraction import (
    ConsistencyReport,
    PaperClaimCandidate,
    PaperExtraction,
)
from genesis_evidence.literature.downloader import DownloadedArtifact
from genesis_evidence.literature.extraction_worker import LiteratureExtractionWorker
from genesis_evidence.literature.ingestion import LiteratureIngestionService
from genesis_evidence.literature.integrity import (
    IntegrityAssessment,
    IntegrityStatus,
)
from genesis_evidence.literature.models import (
    FullTextCandidate,
    FullTextFormat,
    PaperRecord,
    RightsStatus,
    SearchPage,
    SourceAccess,
    SourceName,
)

from .test_jats import JATS_CC_BY

JATS_WITH_RESULT = JATS_CC_BY.replace(
    b"</body>",
    b"<sec><title>Results</title><p>Protein significantly improved muscle strength.</p></sec>"
    b"</body>",
)
JATS_INTERNAL_TDM = JATS_WITH_RESULT.replace(
    b"https://creativecommons.org/licenses/by/4.0/",
    b"https://creativecommons.org/licenses/by-sa/4.0/",
).replace(b"CC BY 4.0", b"CC BY-SA 4.0")


@dataclass
class FakeConnector:
    source = SourceName.EUROPE_PMC
    record: PaperRecord

    def search(self, query: str, *, limit: int = 25, cursor: str | None = None) -> SearchPage:
        del query, limit
        return SearchPage((self.record,)) if cursor is None else SearchPage(())


class FakeDownloader:
    def download(self, candidate: FullTextCandidate) -> DownloadedArtifact:
        return DownloadedArtifact(candidate, JATS_WITH_RESULT, "application/xml", candidate.url)


class InternalTdmDownloader(FakeDownloader):
    def download(self, candidate: FullTextCandidate) -> DownloadedArtifact:
        return DownloadedArtifact(candidate, JATS_INTERNAL_TDM, "application/xml", candidate.url)


class FakeIntegrityChecker:
    def __init__(self, status: IntegrityStatus = IntegrityStatus.CLEAR) -> None:
        self.status = status

    def check(self, paper: dict[str, object]) -> IntegrityAssessment:
        del paper
        return IntegrityAssessment(self.status, (), ("fake",), {"fake": {}})


class FakeAnalyzer:
    model = "fake-extractor"

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def extract(self, paper: PaperRecord, document: dict[str, object]):
        del paper, document
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("provider failed")
        extraction = PaperExtraction(
            summary="The paper reports an intervention effect.",
            research_question="Does protein improve muscle strength?",
            study_design="randomized_controlled_trial",
            population=["Older adults"],
            countries_and_centers="Not reported",
            recruitment_period="Not reported",
            registration_ids=[],
            protocol_status="Not reported",
            statistical_analysis_plan_status="Not reported",
            ethics="Not reported",
            funding="Not reported",
            conflicts_of_interest="Not reported",
            condition_candidates=[],
            directly_reported_symptoms=[],
            studied_approach=["Protein supplementation"],
            comparator=["Placebo"],
            outcomes=["Muscle strength"],
            limitations=[],
            claims=[
                PaperClaimCandidate(
                    text="Protein significantly improved muscle strength.",
                    evidence="Protein significantly improved muscle strength.",
                    locator="Results",
                    claim_type="intervention_effect",
                    inference="causal",
                    population="Older adults",
                    baseline_nutrient_status="Not reported",
                    ingredient_name="Protein",
                    ingredient_form="Protein supplement; form not reported",
                    dose="Not reported",
                    comparator="Placebo",
                    outcome="Muscle strength",
                    timepoint="Not reported",
                    effect_estimate="Improved muscle strength",
                    statistical_details="Not reported",
                )
            ],
        )
        return extraction, f"extract-{self.calls}"

    def check(self, paper, document, extraction, second_extraction):
        del paper, document, extraction, second_extraction
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("provider failed")
        return ConsistencyReport(verdict="consistent", issues=[]), f"check-{self.calls}"


def _record(*, source: SourceName = SourceName.EUROPE_PMC, source_id: str = "MED:123"):
    candidate = FullTextCandidate(
        source=source,
        source_id="PMC123",
        url="https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML",
        format=FullTextFormat.JATS_XML,
        access=SourceAccess.APPROVED_OPEN,
        rights_status=RightsStatus.REDISTRIBUTABLE,
        media_type="application/xml",
    )
    return PaperRecord(
        source=source,
        source_id=source_id,
        title="Nutrition and ageing",
        abstract="A trial reports significantly improved muscle strength in older adults.",
        doi="10.1000/example",
        pmid="123",
        pmcid="PMC123",
        publication_year=2025,
        full_text_candidates=(candidate,),
        raw={"license": "CC BY"},
    )


def _locked_topic(store: PaperStore) -> str:
    topic_id = store.create_topic(
        code="sarcopenia-protein",
        version="1",
        condition_code="COND_SARCOPENIA_FRAILTY",
        review_question="Does protein supplementation improve strength in older adults?",
        picots={
            "population": "Older adults",
            "intervention_or_exposure": "Protein supplementation",
            "comparator": "Placebo or usual diet",
            "outcomes": "Muscle strength",
            "timing": "Any follow-up",
            "setting": "Any human setting",
        },
        eligible_study_designs=("randomized_controlled_trial",),
        inclusion_criteria=("Human adults",),
        exclusion_reasons=("wrong_population", "wrong_intervention", "wrong_design"),
        required_search_streams=("effect",),
        evidence_cutoff_date="2026-08-12",
        reviewer="reviewer-1",
    )
    store.lock_topic(topic_id, reviewer="reviewer-1")
    return topic_id


def test_paper_store_deduplicates_the_same_paper_across_sources(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    first = store.upsert_paper(_record(), source_url="https://example.test/first")
    second = store.upsert_paper(
        _record(source=SourceName.DOAJ, source_id="doaj-1"),
        source_url="https://example.test/second",
    )
    assert first == second
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM papers").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM paper_sources").fetchone()[0] == 2


def test_unverified_repository_record_keeps_unknown_publication_status(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    PaperStore(database).upsert_paper(
        _record(source=SourceName.CORE, source_id="core-1"),
        source_url="https://example.test/core-1",
    )
    with database.connect() as connection:
        status = connection.execute("SELECT publication_status FROM papers").fetchone()[0]
    assert status == "unknown"


def test_object_store_detects_corruption_in_an_existing_digest_path(tmp_path) -> None:
    objects = ObjectStore(tmp_path / "objects")
    stored = objects.put(b"trusted", suffix="xml")
    (objects.root / stored.key).write_bytes(b"corrupt")
    with pytest.raises(OSError, match="corrupt"):
        objects.put(b"trusted", suffix="xml")
    with pytest.raises(OSError, match="corrupt"):
        objects.read(stored.key)


def test_collection_queues_full_text_then_worker_persists_candidate_claims(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(),  # type: ignore[arg-type]
    )
    summary = service.collect(
        topic_id=_locked_topic(store),
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="sarcopenia AND protein",
        limit=5,
    )
    assert summary.discovered == 1
    assert summary.downloaded_full_texts == 1
    assert summary.queued_extractions == 1
    with database.connect() as connection:
        paper = connection.execute("SELECT * FROM papers").fetchone()
        assert paper["integrity_status"] == "clear"
        assert connection.execute("SELECT status FROM collection_runs").fetchone()[0] == "completed"
        assert connection.execute("SELECT processed_at FROM full_texts").fetchone()[0] is None
        assert (
            connection.execute("SELECT status FROM paper_extraction_jobs").fetchone()[0]
            == "queued"
        )
    worker = LiteratureExtractionWorker(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        analyzer=FakeAnalyzer(),
    )
    assert worker.run_once()
    with database.connect() as connection:
        assert connection.execute("SELECT processed_at FROM full_texts").fetchone()[0]
        assert connection.execute("SELECT status FROM paper_admissions").fetchone()[0] == "pending"
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] >= 1
        assert connection.execute("SELECT count(*) FROM results").fetchone()[0] >= 1
        assert connection.execute("SELECT count(*) FROM studies").fetchone()[0] == 1
        extraction = connection.execute("SELECT * FROM paper_extractions").fetchone()
        assert extraction["second_run_id"] == "extract-2"
        job = connection.execute("SELECT * FROM paper_extraction_jobs").fetchone()
        assert job["status"] == "completed"
        assert job["check_run_id"] == "check-3"


def test_pending_full_text_backlog_downloads_and_queues_existing_paper(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _locked_topic(store)
    paper_id = store.upsert_paper(_record(), source_url="https://example.test/paper")
    run_id = store.start_collection(
        topic_id=topic_id,
        source="europe_pmc",
        query="protein AND ageing",
    )
    store.add_to_collection(run_id, paper_id, position=1)
    store.finish_collection(run_id, status="completed", detail={})
    store.screen_collection_paper(
        run_id,
        paper_id,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="ai:screening",
    )
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(),  # type: ignore[arg-type]
    )

    summary = service.retrieve_pending_full_texts()

    assert summary.pending_papers == 1
    assert summary.downloaded_full_texts == 1
    assert summary.queued_extractions == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT full_text_retrieval_status FROM collection_papers"
        ).fetchone()[0] == "retrieved"
        assert connection.execute(
            "SELECT status FROM paper_extraction_jobs"
        ).fetchone()[0] == "queued"


def test_pending_full_text_without_pmcid_closes_retrieval_ledger(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    record = _record()
    record.pmcid = None
    paper_id = store.upsert_paper(record, source_url="https://example.test/paper")
    run_id = store.start_collection(
        topic_id=_locked_topic(store),
        source="europe_pmc",
        query="protein AND ageing",
    )
    store.add_to_collection(run_id, paper_id, position=1)
    store.finish_collection(run_id, status="completed", detail={})
    store.screen_collection_paper(
        run_id,
        paper_id,
        stage="title_abstract",
        decision="included",
        exclusion_reason=None,
        reviewer="ai:screening",
    )
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(),  # type: ignore[arg-type]
    )

    summary = service.retrieve_pending_full_texts()

    assert summary.not_retrieved == 1
    with database.connect() as connection:
        row = connection.execute(
            "SELECT full_text_retrieval_status, full_text_retrieval_reason "
            "FROM collection_papers"
        ).fetchone()
    assert row["full_text_retrieval_status"] == "not_retrieved"
    assert "identifier is unavailable" in row["full_text_retrieval_reason"]


def test_excluded_paper_failure_is_preserved_as_terminal_audit(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    paper_id = store.upsert_paper(_record(), source_url="https://example.test/paper")
    run_id = store.start_collection(
        topic_id=_locked_topic(store),
        source="europe_pmc",
        query="protein AND ageing",
    )
    store.add_to_collection(run_id, paper_id, position=1)
    store.finish_collection(run_id, status="completed", detail={})
    store.screen_collection_paper(
        run_id,
        paper_id,
        stage="title_abstract",
        decision="excluded",
        exclusion_reason="wrong_design",
        reviewer="ai:screening",
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO paper_extraction_jobs(
                id, paper_id, status, stage, error_class, error_message,
                created_at, updated_at
            ) VALUES ('job-1', ?, 'failed', 'extraction_a', 'PaperAnalysisError',
                'invalid extraction', 'now', 'now')
            """,
            (paper_id,),
        )

    assert store.supersede_excluded_extraction_failures(reviewer="ai:retrieval-worker") == 1
    with database.connect() as connection:
        job = connection.execute(
            "SELECT status, error_class, error_message FROM paper_extraction_jobs"
        ).fetchone()
        event = connection.execute(
            "SELECT action, detail_json FROM audit_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert tuple(job) == (
        "failed",
        "ScreeningExcluded",
        "No retry is required because screening excluded the paper.",
    )
    assert event["action"] == "extraction_superseded_by_screening"
    assert "PaperAnalysisError" in event["detail_json"]
    assert store.list_extraction_jobs() == []
    assert store.supersede_excluded_extraction_failures(reviewer="ai:retrieval-worker") == 0
    with database.connect() as connection:
        event_count = connection.execute(
            "SELECT count(*) FROM audit_events WHERE action = 'extraction_superseded_by_screening'"
        ).fetchone()[0]
    assert event_count == 1


def test_worker_failure_keeps_completed_stage_and_can_retry(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(),  # type: ignore[arg-type]
    )
    summary = service.collect(
        topic_id=_locked_topic(store),
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="protein AND ageing",
        limit=1,
    )
    assert summary.downloaded_full_texts == 1
    assert summary.failed_full_texts == 0
    worker = LiteratureExtractionWorker(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        analyzer=FakeAnalyzer(fail_on_call=2),
    )
    job_id = worker.run_once()
    assert job_id
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM full_texts").fetchone()[0] == 1
        job = connection.execute("SELECT * FROM paper_extraction_jobs").fetchone()
        assert job["status"] == "failed"
        assert job["stage"] == "extraction_b"
        assert job["extraction_run_id"] == "extract-1"
        assert job["second_run_id"] is None
    store.retry_extraction(job_id, reviewer="reviewer-1")
    resumed = FakeAnalyzer()
    assert LiteratureExtractionWorker(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        analyzer=resumed,
    ).run_once() == job_id
    assert resumed.calls == 2
    assert store.get_extraction_job(job_id)["status"] == "completed"


def test_targeted_query_batch_is_claimed_before_older_backlog(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    topic_id = _locked_topic(store)
    first = store.upsert_paper(_record(), source_url="https://example.test/first")
    second = store.upsert_paper(
        replace(
            _record(),
            source_id="MED:456",
            doi="10.1000/example-2",
            pmid="456",
            pmcid="PMC456",
        ),
        source_url="https://example.test/second",
    )
    with database.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO collection_runs(
                id, topic_id, condition_code, source, search_stream, query_version,
                query, status, created_at
            ) VALUES (?, ?, 'COND_SARCOPENIA_FRAILTY', 'test', 'effect', ?, 'test', 'completed', ?)
            """,
            (("run-v1", topic_id, "1", "2026-08-12T00:00:00Z"),
             ("run-v2", topic_id, "2", "2026-08-13T00:00:00Z")),
        )
        connection.executemany(
            """
            INSERT INTO paper_extraction_jobs(
                id, paper_id, collection_run_id, status, stage, created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', 'extraction_a', ?, ?)
            """,
            (("job-v1", first, "run-v1", "2026-08-12T00:00:00Z", "2026-08-12T00:00:00Z"),
             ("job-v2", second, "run-v2", "2026-08-13T00:00:00Z", "2026-08-13T00:00:00Z")),
        )
    assert store.claim_next_extraction_job()["id"] == "job-v2"


def test_interrupted_running_job_is_failed_without_losing_saved_stage(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(),  # type: ignore[arg-type]
    )
    service.collect(
        topic_id=_locked_topic(store),
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="protein AND ageing",
        limit=1,
    )
    job = store.claim_next_extraction_job()
    assert job
    extraction, run_id = FakeAnalyzer().extract(_record(), {})
    store.save_extraction_channel(
        str(job["id"]),
        channel="a",
        model="fake-extractor",
        run_id=run_id,
        extraction_json=extraction.model_dump_json(),
    )

    assert store.recover_running_extraction_jobs() == 1
    recovered = store.get_extraction_job(str(job["id"]))
    assert recovered["status"] == "failed"
    assert recovered["stage"] == "extraction_b"
    assert recovered["extraction_run_id"] == "extract-1"
    assert recovered["error_class"] == "WorkerInterrupted"


def test_internal_tdm_full_text_is_processed_but_not_marked_redistributable(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=InternalTdmDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(),  # type: ignore[arg-type]
    )
    summary = service.collect(
        topic_id=_locked_topic(store),
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="protein AND ageing",
        limit=1,
    )
    assert summary.downloaded_full_texts == 1
    with database.connect() as connection:
        full_text = connection.execute("SELECT rights_status FROM full_texts").fetchone()
        assert full_text["rights_status"] == "internal_tdm_only"


def test_retracted_paper_never_enters_full_text_or_claim_processing(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(IntegrityStatus.RETRACTED),  # type: ignore[arg-type]
    )
    summary = service.collect(
        topic_id=_locked_topic(store),
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="sarcopenia",
        limit=1,
    )
    assert summary.skipped_full_texts == 1
    with database.connect() as connection:
        integrity_status = connection.execute("SELECT integrity_status FROM papers").fetchone()[0]
        assert integrity_status == "retracted"
        assert connection.execute("SELECT count(*) FROM full_texts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


def test_expression_of_concern_is_preserved_for_human_review(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(IntegrityStatus.EXPRESSION_OF_CONCERN),  # type: ignore[arg-type]
    )
    service.collect(
        topic_id=_locked_topic(store),
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="sarcopenia",
        limit=1,
        download_full_text=False,
    )
    with database.connect() as connection:
        status = connection.execute("SELECT integrity_status FROM papers").fetchone()[0]
    assert status == "expression_of_concern"


def test_collection_without_completed_integrity_check_preserves_existing_status(tmp_path) -> None:
    class UncheckedIntegrityChecker:
        def check(self, paper: dict[str, object]) -> IntegrityAssessment:
            del paper
            return IntegrityAssessment(IntegrityStatus.UNKNOWN, (), (), {})

    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    paper_id = store.upsert_paper(_record(), source_url="https://example.test/paper")
    store.update_integrity(paper_id, "clear", detail={"checked_sources": ["fake"]})
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=UncheckedIntegrityChecker(),  # type: ignore[arg-type]
    )

    service.collect(
        topic_id=_locked_topic(store),
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="protein AND ageing",
        limit=1,
        download_full_text=False,
    )

    with database.connect() as connection:
        status = connection.execute("SELECT integrity_status FROM papers").fetchone()[0]
    assert status == "clear"
