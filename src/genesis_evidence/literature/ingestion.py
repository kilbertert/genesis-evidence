"""Topic collection into the new evidence persistence boundary."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.store.papers import ObjectStore, PaperStore
from .connectors.base import LiteratureConnector
from .downloader import FullTextDownloader
from .integrity import IntegrityStatus, PublicationIntegrityChecker
from .jats import JatsParser
from .models import FullTextCandidate, FullTextFormat, PaperRecord, RightsStatus


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    run_id: str
    discovered: int
    stored_papers: int
    downloaded_full_texts: int
    queued_extractions: int
    skipped_full_texts: int
    failed_full_texts: int


class LiteratureIngestionService:
    def __init__(
        self,
        *,
        store: PaperStore,
        objects: ObjectStore,
        downloader: FullTextDownloader,
        integrity: PublicationIntegrityChecker,
    ) -> None:
        self._store = store
        self._objects = objects
        self._downloader = downloader
        self._integrity = integrity
        self._jats = JatsParser()

    def collect(
        self,
        *,
        topic_id: str,
        connector: LiteratureConnector,
        query: str,
        limit: int,
        download_full_text: bool = True,
        search_stream: str = "effect",
        query_version: str = "1",
    ) -> IngestionSummary:
        run_id = self._store.start_collection(
            topic_id=topic_id,
            source=connector.source.value,
            query=query,
            search_stream=search_stream,
            query_version=query_version,
        )
        counts = {
            "discovered": 0,
            "stored_papers": 0,
            "downloaded_full_texts": 0,
            "queued_extractions": 0,
            "skipped_full_texts": 0,
            "failed_full_texts": 0,
        }
        cursor: str | None = None
        try:
            while counts["discovered"] < max(1, limit):
                page = connector.search(
                    query,
                    limit=min(100, max(1, limit) - counts["discovered"]),
                    cursor=cursor,
                )
                if not page.records:
                    break
                for record in page.records:
                    if counts["discovered"] >= max(1, limit):
                        break
                    counts["discovered"] += 1
                    paper_id = self._store.upsert_paper(
                        record,
                        source_url=_source_url(record),
                        license_text=str(record.raw.get("license") or "") or None,
                    )
                    counts["stored_papers"] += 1
                    self._store.add_to_collection(
                        run_id,
                        paper_id,
                        position=counts["discovered"],
                    )
                    integrity = self._integrity.check(record.to_dict(include_raw=False))
                    self._store.update_integrity(
                        paper_id,
                        _stored_integrity_status(integrity.status),
                        detail=integrity.to_dict(),
                    )
                    if integrity.status == IntegrityStatus.RETRACTED or not download_full_text:
                        counts["skipped_full_texts"] += len(record.full_text_candidates)
                        continue
                    for candidate in record.full_text_candidates:
                        try:
                            result = self._ingest_candidate(run_id, paper_id, candidate)
                        except Exception as exc:
                            counts["failed_full_texts"] += 1
                            self._store.record_event(
                                "paper",
                                paper_id,
                                "full_text_failed",
                                {
                                    "source": candidate.source.value,
                                    "source_id": candidate.source_id,
                                    "error": f"{type(exc).__name__}: {exc}",
                                },
                            )
                        else:
                            if result is None:
                                counts["skipped_full_texts"] += 1
                            else:
                                counts["downloaded_full_texts"] += 1
                                counts["queued_extractions"] += 1
                                break
                if not page.next_cursor or page.next_cursor == cursor:
                    break
                cursor = page.next_cursor
        except Exception as exc:
            self._store.finish_collection(
                run_id,
                status="failed",
                detail={**counts, "error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        self._store.finish_collection(run_id, status="completed", detail=counts)
        return IngestionSummary(run_id=run_id, **counts)

    def _ingest_candidate(
        self,
        run_id: str,
        paper_id: str,
        candidate: FullTextCandidate,
    ) -> str | None:
        if candidate.format != FullTextFormat.JATS_XML:
            return None
        artifact = self._downloader.download(candidate)
        document = self._jats.parse(artifact.content)
        if document.license.rights_status not in {
            RightsStatus.REDISTRIBUTABLE,
            RightsStatus.INTERNAL_TDM_ONLY,
        }:
            return None
        stored = self._objects.put(artifact.content, suffix="xml")
        self._store.save_full_text(
            paper_id,
            stored,
            media_type=artifact.media_type or "application/xml",
            rights_status=document.license.rights_status.value,
        )
        return self._store.enqueue_extraction(paper_id, collection_run_id=run_id)


def _source_url(record: PaperRecord) -> str:
    if record.doi:
        return f"https://doi.org/{record.doi}"
    if record.source.value == "europe_pmc":
        source, _, identifier = record.source_id.partition(":")
        return f"https://europepmc.org/article/{source}/{identifier}"
    if record.source.value == "doaj":
        return f"https://doaj.org/article/{record.source_id}"
    return f"https://core.ac.uk/works/{record.source_id}"


def _stored_integrity_status(status: IntegrityStatus) -> str:
    return status.value
