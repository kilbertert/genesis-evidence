from __future__ import annotations

from dataclasses import dataclass

import pytest

from genesis_evidence.core.store import Database, ObjectStore, PaperStore
from genesis_evidence.literature.ai_extraction import (
    CheckedPaperExtraction,
    ConsistencyReport,
    PaperClaimCandidate,
    PaperExtraction,
)
from genesis_evidence.literature.downloader import DownloadedArtifact
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
    def analyze(
        self, paper: PaperRecord, document: dict[str, object]
    ) -> CheckedPaperExtraction:
        del paper, document
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
        return CheckedPaperExtraction(
            model="fake-extractor",
            extraction_run_id="extract-1",
            extraction=extraction,
            second_model="fake-extractor-independent",
            second_run_id="extract-2",
            second_extraction=extraction,
            check_model="fake-checker",
            check_run_id="check-1",
            consistency=ConsistencyReport(verdict="consistent", issues=[]),
        )


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


def test_collection_persists_full_text_and_pending_candidate_claims(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    store = PaperStore(database)
    service = LiteratureIngestionService(
        store=store,
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(),  # type: ignore[arg-type]
        analyzer=FakeAnalyzer(),
    )
    summary = service.collect(
        condition_code="COND_SARCOPENIA_FRAILTY",
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="sarcopenia AND protein",
        limit=5,
    )
    assert summary.discovered == 1
    assert summary.downloaded_full_texts == 1
    assert summary.candidate_claims >= 1
    with database.connect() as connection:
        paper = connection.execute("SELECT * FROM papers").fetchone()
        assert paper["integrity_status"] == "clear"
        assert connection.execute("SELECT status FROM collection_runs").fetchone()[0] == "completed"
        assert connection.execute("SELECT processed_at FROM full_texts").fetchone()[0]
        assert connection.execute("SELECT status FROM paper_admissions").fetchone()[0] == "pending"
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] >= 1
        assert connection.execute("SELECT count(*) FROM results").fetchone()[0] >= 1
        assert connection.execute("SELECT count(*) FROM studies").fetchone()[0] == 1
        extraction = connection.execute("SELECT * FROM paper_extractions").fetchone()
        assert extraction["second_run_id"] == "extract-2"


def test_internal_tdm_full_text_is_processed_but_not_marked_redistributable(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    service = LiteratureIngestionService(
        store=PaperStore(database),
        objects=ObjectStore(tmp_path / "objects"),
        downloader=InternalTdmDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(),  # type: ignore[arg-type]
        analyzer=FakeAnalyzer(),
    )
    summary = service.collect(
        condition_code="COND_SARCOPENIA_FRAILTY",
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
    service = LiteratureIngestionService(
        store=PaperStore(database),
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(IntegrityStatus.RETRACTED),  # type: ignore[arg-type]
        analyzer=FakeAnalyzer(),
    )
    summary = service.collect(
        condition_code="COND_SARCOPENIA_FRAILTY",
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="sarcopenia",
        limit=1,
    )
    assert summary.skipped_full_texts == 1
    with database.connect() as connection:
        integrity_status = connection.execute(
            "SELECT integrity_status FROM papers"
        ).fetchone()[0]
        assert integrity_status == "retracted"
        assert connection.execute("SELECT count(*) FROM full_texts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


def test_expression_of_concern_is_preserved_for_human_review(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    service = LiteratureIngestionService(
        store=PaperStore(database),
        objects=ObjectStore(tmp_path / "objects"),
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        integrity=FakeIntegrityChecker(IntegrityStatus.EXPRESSION_OF_CONCERN),  # type: ignore[arg-type]
        analyzer=FakeAnalyzer(),
    )
    service.collect(
        condition_code="COND_SARCOPENIA_FRAILTY",
        connector=FakeConnector(_record()),  # type: ignore[arg-type]
        query="sarcopenia",
        limit=1,
        download_full_text=False,
    )
    with database.connect() as connection:
        status = connection.execute("SELECT integrity_status FROM papers").fetchone()[0]
    assert status == "expression_of_concern"
