"""Persistence for literature collection, full text, and candidate claims."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from ...literature.ai_extraction import CheckedPaperExtraction
from ...literature.models import PaperRecord, SourceName
from .database import Database

SEARCH_STREAMS = {
    "effect",
    "requirement",
    "bioavailability",
    "safety",
    "registration",
    "regulatory",
    "citation",
}
PICOTS_FIELDS = {
    "population",
    "intervention_or_exposure",
    "comparator",
    "outcomes",
    "timing",
    "setting",
}


class PaperIdentityConflict(RuntimeError):
    """Raised when one source record resolves to multiple stored papers."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    sha256: str
    size: int


class ObjectStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    def put(self, content: bytes, *, suffix: str) -> StoredObject:
        normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        if not normalized_suffix[1:].isalnum():
            raise ValueError("Object suffix must be alphanumeric")
        digest = hashlib.sha256(content).hexdigest()
        key = f"sha256/{digest[:2]}/{digest}{normalized_suffix.casefold()}"
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise OSError(f"Content-addressed object is corrupt: {key}")
        else:
            descriptor, temporary_name = tempfile.mkstemp(dir=destination.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return StoredObject(key=key, sha256=digest, size=len(content))

    def read(self, key: str) -> bytes:
        path = (self.root / key).resolve(strict=True)
        path.relative_to(self.root)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != path.stem:
            raise OSError(f"Content-addressed object is corrupt: {key}")
        return content


class PaperStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_topic(
        self,
        *,
        code: str,
        version: str,
        condition_code: str,
        review_question: str,
        picots: dict[str, str],
        eligible_study_designs: tuple[str, ...],
        inclusion_criteria: tuple[str, ...],
        exclusion_reasons: tuple[str, ...],
        required_search_streams: tuple[str, ...],
        evidence_cutoff_date: str,
        reviewer: str,
    ) -> str:
        if set(picots) != PICOTS_FIELDS or any(not value.strip() for value in picots.values()):
            raise ValueError(
                "topic requires complete population, intervention/exposure, comparator, "
                "outcomes, timing, and setting"
            )
        if not all(
            (
                code.strip(),
                version.strip(),
                review_question.strip(),
                eligible_study_designs,
                inclusion_criteria,
                exclusion_reasons,
                required_search_streams,
                evidence_cutoff_date.strip(),
                reviewer.strip(),
            )
        ):
            raise ValueError("topic governance fields are required")
        if any(not value.strip() for value in (*eligible_study_designs, *inclusion_criteria)):
            raise ValueError("topic study designs and inclusion criteria cannot be blank")
        if any(not value.strip() for value in exclusion_reasons) or len(
            set(exclusion_reasons)
        ) != len(exclusion_reasons):
            raise ValueError("topic exclusion reasons must be unique non-blank codes")
        if len(set(required_search_streams)) != len(required_search_streams):
            raise ValueError("topic required search streams must be unique")
        if not set(required_search_streams) <= SEARCH_STREAMS:
            raise ValueError("topic contains an unsupported search stream")
        try:
            cutoff = date.fromisoformat(evidence_cutoff_date)
        except ValueError as exc:
            raise ValueError("topic evidence cutoff date must be a valid ISO date") from exc
        if cutoff > date.today():
            raise ValueError("topic evidence cutoff date cannot be in the future")
        topic_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM conditions WHERE code = ?", (condition_code,)
            ).fetchone():
                raise ValueError("topic contains an unknown condition code")
            connection.execute(
                """
                INSERT INTO evidence_topics(
                    id, code, version, condition_code, status, review_question,
                    picots_json, eligible_study_designs_json, inclusion_criteria_json,
                    exclusion_reasons_json, required_search_streams_json,
                    evidence_cutoff_date, created_by, created_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_id,
                    code,
                    version,
                    condition_code,
                    review_question,
                    json.dumps(picots, ensure_ascii=False),
                    json.dumps(eligible_study_designs, ensure_ascii=False),
                    json.dumps(inclusion_criteria, ensure_ascii=False),
                    json.dumps(exclusion_reasons, ensure_ascii=False),
                    json.dumps(required_search_streams, ensure_ascii=False),
                    evidence_cutoff_date,
                    reviewer,
                    now,
                ),
            )
            self._audit(
                connection,
                "evidence_topic",
                topic_id,
                "topic_created",
                {"code": code, "version": version},
                actor=reviewer,
            )
        return topic_id

    def lock_topic(self, topic_id: str, *, reviewer: str) -> None:
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE evidence_topics SET status = 'locked', locked_by = ?, locked_at = ?
                WHERE id = ? AND status = 'draft'
                """,
                (reviewer, _now(), topic_id),
            ).rowcount
            if updated != 1:
                raise ValueError("topic is missing or is not a draft")
            self._audit(
                connection,
                "evidence_topic",
                topic_id,
                "topic_locked",
                {},
                actor=reviewer,
            )

    def list_topics(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evidence_topics ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [_topic_dict(row) for row in rows]

    def start_collection(
        self,
        *,
        topic_id: str,
        source: str,
        query: str,
        search_stream: str = "effect",
        query_version: str = "1",
    ) -> str:
        if search_stream not in SEARCH_STREAMS:
            raise ValueError("Unsupported literature search stream")
        if not source.strip() or not query.strip() or not query_version.strip():
            raise ValueError("collection source, query, and query version are required")
        run_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            topic = connection.execute(
                "SELECT * FROM evidence_topics WHERE id = ?", (topic_id,)
            ).fetchone()
            if topic is None or topic["status"] != "locked":
                raise ValueError("a locked evidence topic is required before collection")
            if connection.execute(
                "SELECT 1 FROM evidence_profiles WHERE topic_id = ?", (topic_id,)
            ).fetchone():
                raise ValueError("collection is closed after evidence profile creation")
            if search_stream not in json.loads(topic["required_search_streams_json"]):
                raise ValueError("search stream is not required by the locked topic")
            connection.execute(
                """
                INSERT INTO collection_runs(
                    id, topic_id, condition_code, source, search_stream, query_version,
                    query, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    run_id,
                    topic_id,
                    topic["condition_code"],
                    source,
                    search_stream,
                    query_version.strip(),
                    query,
                    _now(),
                ),
            )
        return run_id

    def finish_collection(self, run_id: str, *, status: str, detail: dict[str, object]) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Collection status must be completed or failed")
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE collection_runs SET status = ?, completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (status, _now(), run_id),
            ).rowcount
            if updated != 1:
                raise ValueError("Collection run is missing or already finished")
            self._audit(connection, "collection_run", run_id, status, detail)

    def upsert_paper(
        self,
        record: PaperRecord,
        *,
        source_url: str,
        license_text: str | None = None,
    ) -> str:
        with self.database.transaction() as connection:
            matches = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT paper_id FROM paper_sources WHERE source = ? AND source_id = ?
                    UNION
                    SELECT id FROM papers WHERE (? <> '' AND doi = ?)
                        OR (? <> '' AND pmid = ?) OR (? <> '' AND pmcid = ?)
                    """,
                    (
                        record.source.value,
                        record.source_id,
                        record.doi or "",
                        record.doi or "",
                        record.pmid or "",
                        record.pmid or "",
                        record.pmcid or "",
                        record.pmcid or "",
                    ),
                ).fetchall()
            }
            if len(matches) > 1:
                raise PaperIdentityConflict("Paper identifiers resolve to different stored papers")
            paper_id = next(iter(matches), str(uuid.uuid4()))
            connection.execute(
                """
                INSERT INTO papers(
                    id, title, abstract, doi, pmid, pmcid, year, publication_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = CASE WHEN length(excluded.title) > length(papers.title)
                        THEN excluded.title ELSE papers.title END,
                    abstract = CASE WHEN length(excluded.abstract) > length(papers.abstract)
                        THEN excluded.abstract ELSE papers.abstract END,
                    doi = COALESCE(papers.doi, excluded.doi),
                    pmid = COALESCE(papers.pmid, excluded.pmid),
                    pmcid = COALESCE(papers.pmcid, excluded.pmcid),
                    year = COALESCE(papers.year, excluded.year),
                    publication_status = CASE
                        WHEN papers.publication_status = 'formal' THEN 'formal'
                        ELSE excluded.publication_status END
                """,
                (
                    paper_id,
                    record.title,
                    record.abstract or "",
                    record.doi,
                    record.pmid,
                    record.pmcid,
                    record.publication_year,
                    _publication_status(record),
                    _now(),
                ),
            )
            connection.execute(
                """
                INSERT INTO paper_sources(paper_id, source, source_id, source_url, license)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(paper_id, source, source_id) DO UPDATE SET
                    source_url = excluded.source_url, license = excluded.license
                """,
                (paper_id, record.source.value, record.source_id, source_url, license_text),
            )
        return paper_id

    def add_to_collection(self, run_id: str, paper_id: str, *, position: int) -> None:
        with self.database.transaction() as connection:
            run = connection.execute(
                "SELECT status FROM collection_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None or run["status"] != "running":
                raise ValueError("papers can only be added to a running collection")
            connection.execute(
                """
                INSERT INTO collection_papers(run_id, paper_id, position) VALUES (?, ?, ?)
                ON CONFLICT(run_id, paper_id) DO UPDATE SET
                    position = MIN(position, excluded.position)
                """,
                (run_id, paper_id, position),
            )

    def screen_collection_paper(
        self,
        run_id: str,
        paper_id: str,
        *,
        stage: str,
        decision: str,
        exclusion_reason: str | None,
        reviewer: str,
    ) -> None:
        if stage not in {"title_abstract", "full_text"}:
            raise ValueError("screening stage must be title_abstract or full_text")
        if decision not in {"included", "excluded"}:
            raise ValueError("screening decision must be included or excluded")
        reason = (exclusion_reason or "").strip() or None
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT cp.*, cr.status AS run_status, cr.topic_id, et.status AS topic_status,
                    et.exclusion_reasons_json
                FROM collection_papers cp
                JOIN collection_runs cr ON cr.id = cp.run_id
                JOIN evidence_topics et ON et.id = cr.topic_id
                WHERE cp.run_id = ? AND cp.paper_id = ?
                """,
                (run_id, paper_id),
            ).fetchone()
            if row is None:
                raise ValueError("collection paper was not found")
            if row["run_status"] != "completed" or row["topic_status"] != "locked":
                raise ValueError("screening requires a completed run for a locked topic")
            if connection.execute(
                """
                SELECT 1 FROM evidence_profiles ep JOIN collection_runs cr
                    ON cr.topic_id = ep.topic_id
                WHERE cr.id = ? LIMIT 1
                """,
                (run_id,),
            ).fetchone():
                raise ValueError("screening is immutable after evidence profile creation")
            allowed_reasons = set(json.loads(row["exclusion_reasons_json"]))
            if decision == "excluded" and reason not in allowed_reasons:
                raise ValueError("an excluded record requires one catalogued primary reason")
            if decision == "included" and reason is not None:
                raise ValueError("an included record cannot have an exclusion reason")
            now = _now()
            if stage == "title_abstract":
                updated = connection.execute(
                    """
                    UPDATE collection_papers SET title_abstract_decision = ?,
                        title_abstract_reviewer = ?, title_abstract_reviewed_at = ?,
                        full_text_decision = NULL, full_text_reviewer = NULL,
                        full_text_reviewed_at = NULL, primary_exclusion_reason = ?
                    WHERE paper_id = ? AND run_id IN (
                            SELECT id FROM collection_runs
                            WHERE topic_id = ? AND status = 'completed'
                    )
                    """,
                    (decision, reviewer, now, reason, paper_id, row["topic_id"]),
                )
            else:
                if row["title_abstract_decision"] != "included":
                    raise ValueError("full-text screening requires title/abstract inclusion")
                full_text = connection.execute(
                    "SELECT 1 FROM full_texts WHERE paper_id = ?", (paper_id,)
                ).fetchone()
                if full_text is None:
                    raise ValueError("full-text screening requires a stored full text")
                if row["full_text_retrieval_status"] != "retrieved":
                    connection.execute(
                        """
                        UPDATE collection_papers SET full_text_retrieval_status = 'retrieved',
                            full_text_retrieval_reason = NULL,
                            full_text_retrieval_reviewer = ?,
                            full_text_retrieval_recorded_at = ?
                        WHERE paper_id = ? AND run_id IN (
                            SELECT id FROM collection_runs
                            WHERE topic_id = ? AND status = 'completed'
                        )
                        """,
                        (reviewer, now, paper_id, row["topic_id"]),
                    )
                    self._audit(
                        connection,
                        "collection_paper",
                        f"{run_id}:{paper_id}",
                        "full_text_retrieval_recorded",
                        {"status": "retrieved", "reason": None},
                        actor=reviewer,
                    )
                updated = connection.execute(
                    """
                    UPDATE collection_papers SET full_text_decision = ?,
                        primary_exclusion_reason = ?, full_text_reviewer = ?,
                        full_text_reviewed_at = ? WHERE paper_id = ? AND run_id IN (
                            SELECT id FROM collection_runs
                            WHERE topic_id = ? AND status = 'completed'
                        )
                    """,
                    (decision, reason, reviewer, now, paper_id, row["topic_id"]),
                )
            self._audit(
                connection,
                "collection_paper",
                f"{run_id}:{paper_id}",
                f"{stage}_screened",
                {
                    "from_decision": row[f"{stage}_decision"],
                    "to_decision": decision,
                    "primary_exclusion_reason": reason,
                    "propagated_collection_records": updated.rowcount,
                },
                actor=reviewer,
            )

    def record_full_text_retrieval(
        self,
        run_id: str,
        paper_id: str,
        *,
        status: str,
        reason: str | None,
        reviewer: str,
    ) -> None:
        if status not in {"pending", "retrieved", "not_retrieved"}:
            raise ValueError("full-text retrieval status is invalid")
        normalized_reason = (reason or "").strip() or None
        if status == "not_retrieved" and normalized_reason is None:
            raise ValueError("not-retrieved full text requires a reason")
        if status != "not_retrieved" and normalized_reason is not None:
            raise ValueError("only not-retrieved full text can have a retrieval reason")
        if normalized_reason and len(normalized_reason) > 2000:
            raise ValueError("full-text retrieval reason is too long")
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT cp.title_abstract_decision, cr.status AS run_status, cr.topic_id,
                    et.status AS topic_status
                FROM collection_papers cp
                JOIN collection_runs cr ON cr.id = cp.run_id
                JOIN evidence_topics et ON et.id = cr.topic_id
                WHERE cp.run_id = ? AND cp.paper_id = ?
                """,
                (run_id, paper_id),
            ).fetchone()
            if row is None:
                raise ValueError("collection paper was not found")
            if row["run_status"] != "completed" or row["topic_status"] != "locked":
                raise ValueError("retrieval recording requires a completed run for a locked topic")
            if row["title_abstract_decision"] != "included":
                raise ValueError("full-text retrieval requires title/abstract inclusion")
            if connection.execute(
                """
                SELECT 1 FROM evidence_profiles ep JOIN collection_runs cr
                    ON cr.topic_id = ep.topic_id
                WHERE cr.id = ? LIMIT 1
                """,
                (run_id,),
            ).fetchone():
                raise ValueError("retrieval is immutable after evidence profile creation")
            has_full_text = connection.execute(
                "SELECT 1 FROM full_texts WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if status == "retrieved" and has_full_text is None:
                raise ValueError("retrieved status requires a stored full text")
            if status != "retrieved" and has_full_text is not None:
                raise ValueError("a stored full text must be recorded as retrieved")
            now = _now()
            updated = connection.execute(
                """
                UPDATE collection_papers SET full_text_retrieval_status = ?,
                    full_text_retrieval_reason = ?,
                    full_text_retrieval_reviewer = ?,
                    full_text_retrieval_recorded_at = ?,
                    full_text_decision = CASE WHEN ? = 'retrieved'
                        THEN full_text_decision ELSE NULL END,
                    primary_exclusion_reason = CASE WHEN ? = 'retrieved'
                        THEN primary_exclusion_reason ELSE NULL END,
                    full_text_reviewer = CASE WHEN ? = 'retrieved'
                        THEN full_text_reviewer ELSE NULL END,
                    full_text_reviewed_at = CASE WHEN ? = 'retrieved'
                        THEN full_text_reviewed_at ELSE NULL END
                WHERE paper_id = ? AND run_id IN (
                    SELECT id FROM collection_runs
                    WHERE topic_id = ? AND status = 'completed'
                )
                """,
                (
                    status,
                    normalized_reason,
                    reviewer,
                    now,
                    status,
                    status,
                    status,
                    status,
                    paper_id,
                    row["topic_id"],
                ),
            )
            self._audit(
                connection,
                "collection_paper",
                f"{run_id}:{paper_id}",
                "full_text_retrieval_recorded",
                {
                    "status": status,
                    "reason": normalized_reason,
                    "propagated_collection_records": updated.rowcount,
                },
                actor=reviewer,
            )

    def reconcile_topic_ledger(self, topic_id: str, *, reviewer: str) -> dict[str, object]:
        """Normalize duplicated collection records without changing a published topic.

        Collection runs remain the PRISMA audit trail.  The screening conclusion is
        nevertheless a topic/paper fact, so missing duplicate records can inherit a
        single recorded conclusion.  Conflicting completed conclusions are reported
        for exception handling and never guessed away.
        """

        with self.database.transaction() as connection:
            topic = connection.execute(
                "SELECT status FROM evidence_topics WHERE id = ?", (topic_id,)
            ).fetchone()
            if topic is None or topic["status"] != "locked":
                raise ValueError("a locked evidence topic is required for ledger reconciliation")
            if connection.execute(
                "SELECT 1 FROM evidence_profiles WHERE topic_id = ?", (topic_id,)
            ).fetchone():
                raise ValueError(
                    "ledger reconciliation is immutable after evidence profile creation"
                )
            rows = connection.execute(
                """
                SELECT cp.*, cr.created_at AS run_created_at
                FROM collection_papers cp
                JOIN collection_runs cr ON cr.id = cp.run_id
                WHERE cr.topic_id = ? AND cr.status = 'completed'
                ORDER BY cp.paper_id, cr.created_at, cp.run_id
                """,
                (topic_id,),
            ).fetchall()
            grouped: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                grouped.setdefault(str(row["paper_id"]), []).append(row)
            normalized = 0
            conflicts: list[dict[str, object]] = []
            for paper_id, records in grouped.items():
                titles = {
                    str(row["title_abstract_decision"])
                    for row in records
                    if row["title_abstract_decision"]
                }
                full_text = {
                    str(row["full_text_decision"])
                    for row in records
                    if row["full_text_decision"]
                }
                if len(titles) > 1 or len(full_text) > 1:
                    conflicts.append(
                        {
                            "paper_id": paper_id,
                            "title_abstract_decisions": sorted(titles),
                            "full_text_decisions": sorted(full_text),
                        }
                    )
                    continue
                has_stored_full_text = connection.execute(
                    "SELECT 1 FROM full_texts WHERE paper_id = ?", (paper_id,)
                ).fetchone()
                retrieval = "retrieved" if has_stored_full_text else None
                retrieval_reason = None
                retrieval_reviewer = reviewer
                if retrieval is None:
                    closed = [
                        row
                        for row in records
                        if row["full_text_retrieval_status"] == "not_retrieved"
                        and str(row["full_text_retrieval_reason"] or "").strip()
                    ]
                    if closed:
                        canonical = closed[-1]
                        retrieval = "not_retrieved"
                        retrieval_reason = str(canonical["full_text_retrieval_reason"])
                        retrieval_reviewer = str(
                            canonical["full_text_retrieval_reviewer"] or reviewer
                        )
                canonical_title = next(iter(titles), None)
                canonical_full_text = next(iter(full_text), None)
                if canonical_title is None and retrieval is None and canonical_full_text is None:
                    continue
                now = _now()
                assignments: list[str] = []
                values: list[object] = []
                if canonical_title is not None:
                    exclusion_reason = next(
                        (
                            str(row["primary_exclusion_reason"])
                            for row in records
                            if canonical_title == "excluded"
                            and row["title_abstract_decision"] == "excluded"
                            and row["primary_exclusion_reason"]
                        ),
                        None,
                    )
                    assignments.extend(
                        (
                            "title_abstract_decision = ?",
                            "title_abstract_reviewer = ?",
                            "title_abstract_reviewed_at = ?",
                        )
                    )
                    values.extend((canonical_title, reviewer, now))
                    if canonical_title == "excluded":
                        assignments.extend(
                            (
                                "full_text_decision = NULL",
                                "full_text_reviewer = NULL",
                                "full_text_reviewed_at = NULL",
                                "primary_exclusion_reason = ?",
                            )
                        )
                        values.append(exclusion_reason)
                if retrieval is not None:
                    assignments.extend(
                        (
                            "full_text_retrieval_status = ?",
                            "full_text_retrieval_reason = ?",
                            "full_text_retrieval_reviewer = ?",
                            "full_text_retrieval_recorded_at = ?",
                        )
                    )
                    values.extend((retrieval, retrieval_reason, retrieval_reviewer, now))
                    if retrieval == "not_retrieved":
                        assignments.extend(
                            (
                                "full_text_decision = NULL",
                                "full_text_reviewer = NULL",
                                "full_text_reviewed_at = NULL",
                                "primary_exclusion_reason = NULL",
                            )
                        )
                if canonical_full_text is not None:
                    exclusion_reason = next(
                        (
                            str(row["primary_exclusion_reason"])
                            for row in records
                            if row["primary_exclusion_reason"]
                        ),
                        None,
                    )
                    assignments.extend(
                        (
                            "full_text_decision = ?",
                            "primary_exclusion_reason = ?",
                            "full_text_reviewer = ?",
                            "full_text_reviewed_at = ?",
                        )
                    )
                    values.extend(
                        (
                            canonical_full_text,
                            exclusion_reason if canonical_full_text == "excluded" else None,
                            reviewer,
                            now,
                        )
                    )
                result = connection.execute(
                    "UPDATE collection_papers SET "
                    + ", ".join(assignments)
                    + " WHERE paper_id = ? AND run_id IN ("
                    "SELECT id FROM collection_runs WHERE topic_id = ? AND status = 'completed'"
                    ")",
                    (*values, paper_id, topic_id),
                )
                normalized += result.rowcount
                self._audit(
                    connection,
                    "paper",
                    paper_id,
                    "topic_ledger_reconciled",
                    {
                        "topic_id": topic_id,
                        "title_abstract_decision": canonical_title,
                        "full_text_retrieval_status": retrieval,
                        "full_text_decision": canonical_full_text,
                        "collection_records": result.rowcount,
                    },
                    actor=reviewer,
                )
        return {"topic_id": topic_id, "normalized_records": normalized, "conflicts": conflicts}

    def list_pending_full_texts(self) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT cp.run_id, cp.paper_id, p.pmcid
                FROM collection_papers cp
                JOIN collection_runs cr ON cr.id = cp.run_id
                JOIN evidence_topics et ON et.id = cr.topic_id
                JOIN papers p ON p.id = cp.paper_id
                WHERE cp.title_abstract_decision = 'included'
                    AND cp.full_text_retrieval_status = 'pending'
                    AND cr.status = 'completed' AND et.status = 'locked'
                ORDER BY cp.paper_id, cp.run_id
                """
            ).fetchall()
        pending: dict[str, dict[str, object]] = {}
        for row in rows:
            item = pending.setdefault(
                str(row["paper_id"]),
                {
                    "paper_id": str(row["paper_id"]),
                    "pmcid": str(row["pmcid"] or ""),
                    "run_ids": [],
                },
            )
            item["run_ids"].append(str(row["run_id"]))  # type: ignore[union-attr]
        return list(pending.values())

    def supersede_excluded_extraction_failures(self, *, reviewer: str) -> int:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT job.id, job.paper_id, job.error_class, job.error_message
                FROM paper_extraction_jobs job
                WHERE job.status IN ('failed', 'queued')
                    AND (job.error_class IS NULL OR job.error_class <> 'ScreeningExcluded')
                    AND EXISTS (
                        SELECT 1 FROM collection_papers cp
                        WHERE cp.paper_id = job.paper_id
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM collection_papers cp
                        WHERE cp.paper_id = job.paper_id
                            AND cp.title_abstract_decision = 'included'
                            AND COALESCE(cp.full_text_decision, 'included') <> 'excluded'
                    )
                """
            ).fetchall()
            now = _now()
            for row in rows:
                connection.execute(
                    """
                    UPDATE paper_extraction_jobs SET status = 'failed',
                        error_class = 'ScreeningExcluded', error_message = ?, updated_at = ?
                        WHERE id = ?
                    """,
                    (
                        "No retry is required because screening excluded the paper.",
                        now,
                        row["id"],
                    ),
                )
                self._audit(
                    connection,
                    "paper_extraction_job",
                    row["id"],
                    "extraction_superseded_by_screening",
                    {
                        "paper_id": row["paper_id"],
                        "previous_error_class": row["error_class"],
                        "previous_error_message": row["error_message"],
                    },
                    actor=reviewer,
                )
        return len(rows)

    def list_topic_ledger(self, topic_id: str) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT cp.*, cr.source, cr.search_stream, cr.query_version, cr.query,
                    cr.status AS run_status, cr.completed_at, p.title, p.doi, p.pmid,
                    p.pmcid, p.year
                FROM collection_papers cp
                JOIN collection_runs cr ON cr.id = cp.run_id
                JOIN papers p ON p.id = cp.paper_id
                WHERE cr.topic_id = ?
                ORDER BY cr.created_at, cp.position, cp.paper_id
                """,
                (topic_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_integrity(self, paper_id: str, status: str, *, detail: dict[str, object]) -> None:
        if status not in {
            "clear",
            "updated",
            "corrected",
            "expression_of_concern",
            "retracted",
            "unknown",
        }:
            raise ValueError(f"Unsupported integrity status: {status}")
        with self.database.transaction() as connection:
            paper = connection.execute(
                "SELECT integrity_status FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if paper is None:
                raise ValueError("paper not found")
            connection.execute(
                "UPDATE papers SET integrity_status = ? WHERE id = ?",
                (status, paper_id),
            )
            stale_cards = 0
            if status != "clear":
                stale_cards = connection.execute(
                    """
                    UPDATE knowledge_cards SET status = 'stale'
                    WHERE status IN ('draft', 'in_review', 'approved', 'published') AND id IN (
                        SELECT cc.card_id FROM card_claims cc
                        JOIN claims c ON c.id = cc.claim_id
                        WHERE c.paper_id = ?
                    )
                    """,
                    (paper_id,),
                ).rowcount
            self._audit(
                connection,
                "paper",
                paper_id,
                f"integrity_{status}",
                {
                    **detail,
                    "from_status": paper["integrity_status"],
                    "to_status": status,
                    "stale_cards": stale_cards,
                },
            )

    def save_full_text(
        self,
        paper_id: str,
        stored: StoredObject,
        *,
        media_type: str,
        rights_status: str,
        collection_run_id: str | None = None,
    ) -> None:
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO full_texts(paper_id, object_key, sha256, media_type, rights_status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    object_key = excluded.object_key, sha256 = excluded.sha256,
                    media_type = excluded.media_type, rights_status = excluded.rights_status,
                    processed_at = NULL
                """,
                (paper_id, stored.key, stored.sha256, media_type, rights_status),
            )
            retrieval_query = """
                UPDATE collection_papers SET full_text_retrieval_status = 'retrieved',
                    full_text_retrieval_reason = NULL,
                    full_text_retrieval_reviewer = 'system:ingestion',
                    full_text_retrieval_recorded_at = ?
                WHERE paper_id = ? AND (? IS NULL OR run_id = ?)
            """
            updated = connection.execute(
                retrieval_query,
                (now, paper_id, collection_run_id, collection_run_id),
            ).rowcount
            self._audit(
                connection,
                "paper",
                paper_id,
                "full_text_retrieval_recorded",
                {"status": "retrieved", "collection_records": updated},
            )

    def enqueue_extraction(self, paper_id: str, *, collection_run_id: str | None) -> str:
        now = _now()
        with self.database.transaction() as connection:
            full_text = connection.execute(
                "SELECT 1 FROM full_texts WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if full_text is None:
                raise ValueError("paper extraction requires a stored full text")
            active = connection.execute(
                """
                SELECT id FROM paper_extraction_jobs
                WHERE paper_id = ? AND status IN ('queued', 'running')
                """,
                (paper_id,),
            ).fetchone()
            if active:
                return str(active["id"])
            job_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO paper_extraction_jobs(
                    id, paper_id, collection_run_id, status, stage, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', 'extraction_a', ?, ?)
                """,
                (job_id, paper_id, collection_run_id, now, now),
            )
            self._audit(
                connection,
                "paper_extraction_job",
                job_id,
                "extraction_queued",
                {"paper_id": paper_id, "collection_run_id": collection_run_id},
            )
        return job_id

    def claim_next_extraction_job(self) -> dict[str, object] | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM paper_extraction_jobs
                WHERE status = 'queued'
                    AND (NOT EXISTS (
                        SELECT 1 FROM collection_papers cp
                        WHERE cp.paper_id = paper_extraction_jobs.paper_id
                    ) OR EXISTS (
                        SELECT 1 FROM collection_papers cp
                        WHERE cp.paper_id = paper_extraction_jobs.paper_id
                            AND COALESCE(cp.title_abstract_decision, 'included') <> 'excluded'
                            AND COALESCE(cp.full_text_decision, 'included') <> 'excluded'
                    ))
                ORDER BY CASE WHEN EXISTS (
                    SELECT 1 FROM collection_runs run
                    WHERE run.id = paper_extraction_jobs.collection_run_id
                        AND run.query_version = '2'
                ) THEN 0 ELSE 1 END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM collection_runs run
                    JOIN evidence_topics topic ON topic.id = run.topic_id
                    WHERE run.id = paper_extraction_jobs.collection_run_id
                        AND run.query_version = '2'
                        AND (
                            SELECT COUNT(*) FROM paper_extraction_jobs done
                            JOIN collection_runs done_run ON done_run.id = done.collection_run_id
                            JOIN evidence_topics done_topic ON done_topic.id = done_run.topic_id
                            WHERE done.status = 'completed'
                                AND done_topic.condition_code = topic.condition_code
                        ) = 0
                ) THEN 0 ELSE 1 END,
                created_at, id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            now = _now()
            connection.execute(
                """
                UPDATE paper_extraction_jobs SET status = 'running',
                    attempt_count = attempt_count + 1, started_at = ?, updated_at = ?,
                    error_class = NULL, error_message = NULL
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM paper_extraction_jobs WHERE id = ?", (row["id"],)
            ).fetchone()
        return dict(claimed)

    def recover_running_extraction_jobs(self) -> int:
        with self.database.transaction() as connection:
            now = _now()
            rows = connection.execute(
                "SELECT id FROM paper_extraction_jobs WHERE status = 'running'"
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE paper_extraction_jobs SET status = 'failed',
                        error_class = 'WorkerInterrupted',
                        error_message = 'Worker stopped before the current stage was persisted.',
                        updated_at = ? WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                self._audit(
                    connection,
                    "paper_extraction_job",
                    row["id"],
                    "extraction_interrupted",
                    {},
                )
        return len(rows)

    def retry_extraction(self, job_id: str, *, reviewer: str) -> None:
        with self.database.transaction() as connection:
            job = connection.execute(
                "SELECT paper_id, status FROM paper_extraction_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None or job["status"] != "failed":
                raise ValueError("only a failed extraction job can be retried")
            if connection.execute(
                """
                SELECT 1 FROM paper_extraction_jobs
                WHERE paper_id = ? AND status IN ('queued', 'running')
                """,
                (job["paper_id"],),
            ).fetchone():
                raise ValueError("paper already has an active extraction job")
            updated = connection.execute(
                """
                UPDATE paper_extraction_jobs SET status = 'queued', started_at = NULL,
                    updated_at = ?, error_class = NULL, error_message = NULL
                WHERE id = ? AND status = 'failed'
                """,
                (_now(), job_id),
            ).rowcount
            if updated != 1:
                raise ValueError("only a failed extraction job can be retried")
            self._audit(
                connection,
                "paper_extraction_job",
                job_id,
                "extraction_retried",
                {},
                actor=reviewer,
            )

    def save_extraction_channel(
        self,
        job_id: str,
        *,
        channel: str,
        model: str,
        run_id: str,
        extraction_json: str,
    ) -> None:
        if channel not in {"a", "b"}:
            raise ValueError("extraction channel must be a or b")
        fields = (
            ("model", "extraction_run_id", "extraction_json", "extraction_b")
            if channel == "a"
            else ("second_model", "second_run_id", "second_extraction_json", "consistency")
        )
        with self.database.transaction() as connection:
            updated = connection.execute(
                f"""
                UPDATE paper_extraction_jobs SET {fields[0]} = ?, {fields[1]} = ?,
                    {fields[2]} = ?, stage = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (model, run_id, extraction_json, fields[3], _now(), job_id),
            ).rowcount
            if updated != 1:
                raise ValueError("extraction job is not running")

    def save_extraction_consistency(
        self,
        job_id: str,
        *,
        model: str,
        run_id: str,
        consistency_json: str,
    ) -> None:
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE paper_extraction_jobs SET check_model = ?, check_run_id = ?,
                    consistency_json = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND stage = 'consistency'
                """,
                (model, run_id, consistency_json, _now(), job_id),
            ).rowcount
            if updated != 1:
                raise ValueError("extraction consistency stage is not running")

    def complete_extraction_job(self, job_id: str) -> None:
        with self.database.transaction() as connection:
            now = _now()
            updated = connection.execute(
                """
                UPDATE paper_extraction_jobs SET status = 'completed', stage = 'saved',
                    updated_at = ?, completed_at = ?
                WHERE id = ? AND status = 'running' AND consistency_json IS NOT NULL
                """,
                (now, now, job_id),
            ).rowcount
            if updated != 1:
                raise ValueError("extraction job cannot be completed")
            self._audit(
                connection,
                "paper_extraction_job",
                job_id,
                "extraction_completed",
                {},
            )

    def fail_extraction_job(self, job_id: str, error: Exception) -> None:
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE paper_extraction_jobs SET status = 'failed', error_class = ?,
                    error_message = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (type(error).__name__, str(error)[:4000], _now(), job_id),
            ).rowcount
            if updated != 1:
                return
            self._audit(
                connection,
                "paper_extraction_job",
                job_id,
                "extraction_failed",
                {"error_class": type(error).__name__, "error_message": str(error)[:4000]},
            )

    def list_extraction_jobs(self, *, limit: int = 100) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT pej.id, pej.paper_id, pej.collection_run_id, pej.status, pej.stage,
                    pej.attempt_count, pej.model, pej.extraction_run_id, pej.second_model,
                    pej.second_run_id, pej.check_model, pej.check_run_id, pej.error_class,
                    pej.error_message, pej.created_at, pej.started_at, pej.updated_at,
                    pej.completed_at, p.title, p.doi
                FROM paper_extraction_jobs pej
                JOIN papers p ON p.id = pej.paper_id
                WHERE pej.error_class IS NULL OR pej.error_class <> 'ScreeningExcluded'
                ORDER BY pej.created_at DESC, pej.id DESC LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_extraction_job(self, job_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_extraction_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_analysis_source(self, paper_id: str) -> tuple[PaperRecord, str]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT p.*, ps.source, ps.source_id, ft.object_key
                FROM papers p JOIN full_texts ft ON ft.paper_id = p.id
                LEFT JOIN paper_sources ps ON ps.rowid = (
                    SELECT rowid FROM paper_sources WHERE paper_id = p.id
                    ORDER BY source, source_id LIMIT 1
                )
                WHERE p.id = ?
                """,
                (paper_id,),
            ).fetchone()
        if row is None:
            raise ValueError("paper or full text does not exist")
        source = SourceName(row["source"] or "europe_pmc")
        paper = PaperRecord(
            source=source,
            source_id=row["source_id"] or paper_id,
            title=row["title"],
            abstract=row["abstract"] or None,
            doi=row["doi"],
            pmid=row["pmid"],
            pmcid=row["pmcid"],
            publication_year=row["year"],
        )
        return paper, str(row["object_key"])

    def save_ai_extraction(self, paper_id: str, checked: CheckedPaperExtraction) -> int:
        now = _now()
        extraction_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            paper = connection.execute(
                "SELECT integrity_status FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if paper is None:
                raise ValueError("Paper does not exist")
            if paper["integrity_status"] == "retracted":
                raise ValueError("Candidate claims cannot be stored for a retracted paper")
            existing = connection.execute(
                """
                SELECT id FROM paper_extractions
                WHERE paper_id = ? AND extraction_run_id = ?
                """,
                (paper_id, checked.extraction_run_id),
            ).fetchone()
            if existing:
                return connection.execute(
                    "SELECT count(*) FROM claims WHERE extraction_id = ?",
                    (existing["id"],),
                ).fetchone()[0]
            connection.execute(
                "UPDATE papers SET study_design_candidate = ? WHERE id = ?",
                (checked.extraction.study_design, paper_id),
            )
            connection.execute(
                """
                INSERT INTO paper_extractions(
                    id, paper_id, model, extraction_run_id, extraction_json,
                    second_model, second_run_id, second_extraction_json,
                    check_model, check_run_id, consistency_status, consistency_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    extraction_id,
                    paper_id,
                    checked.model,
                    checked.extraction_run_id,
                    checked.extraction.model_dump_json(),
                    checked.second_model,
                    checked.second_run_id,
                    checked.second_extraction.model_dump_json(),
                    checked.check_model,
                    checked.check_run_id,
                    checked.consistency.verdict,
                    checked.consistency.model_dump_json(),
                    now,
                ),
            )
            study_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"genesis-study:{paper_id}"))
            connection.execute(
                """
                INSERT INTO studies(
                    id, study_design, registration_ids_json, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    study_design = excluded.study_design,
                    registration_ids_json = excluded.registration_ids_json
                """,
                (
                    study_id,
                    checked.extraction.study_design,
                    json.dumps(checked.extraction.registration_ids, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO study_publications(study_id, paper_id, role)
                VALUES (?, ?, 'primary') ON CONFLICT(study_id, paper_id) DO NOTHING
                """,
                (study_id, paper_id),
            )
            candidate_result_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT result_id FROM claims WHERE paper_id = ? AND status = 'candidate'",
                    (paper_id,),
                ).fetchall()
                if row[0]
            ]
            connection.execute(
                "DELETE FROM claims WHERE paper_id = ? AND status = 'candidate'",
                (paper_id,),
            )
            if candidate_result_ids:
                connection.execute(
                    f"DELETE FROM results WHERE id IN ({_placeholders(candidate_result_ids)})",
                    candidate_result_ids,
                )
            inserted = 0
            for claim in checked.extraction.claims:
                result_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{extraction_id}\n{claim.locator}\n{claim.evidence}\nresult",
                    )
                )
                claim_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{extraction_id}\n{claim.locator}\n{claim.evidence}\n{claim.text}",
                    )
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO results(
                        id, study_id, paper_id, extraction_id, population,
                        baseline_nutrient_status, ingredient_name, ingredient_form,
                        dose, comparator, outcome, timepoint, effect_estimate,
                        statistical_details, evidence_text, locator, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_id,
                        study_id,
                        paper_id,
                        extraction_id,
                        claim.population,
                        claim.baseline_nutrient_status,
                        claim.ingredient_name,
                        claim.ingredient_form,
                        claim.dose,
                        claim.comparator,
                        claim.outcome,
                        claim.timepoint,
                        claim.effect_estimate,
                        claim.statistical_details,
                        claim.evidence,
                        claim.locator,
                        now,
                    ),
                )
                inserted += connection.execute(
                    """
                    INSERT OR IGNORE INTO claims(
                        id, paper_id, extraction_id, result_id, candidate_text,
                        evidence_text, locator, candidate_claim_type,
                        candidate_study_design, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim_id,
                        paper_id,
                        extraction_id,
                        result_id,
                        claim.text,
                        claim.evidence,
                        claim.locator,
                        claim.claim_type,
                        checked.extraction.study_design,
                        now,
                    ),
                ).rowcount
            connection.execute(
                """
                INSERT INTO paper_admissions(paper_id, status) VALUES (?, 'pending')
                ON CONFLICT(paper_id) DO NOTHING
                """,
                (paper_id,),
            )
            connection.execute(
                "UPDATE full_texts SET processed_at = ? WHERE paper_id = ?",
                (now, paper_id),
            )
            self._audit(
                connection,
                "paper",
                paper_id,
                "candidate_claims_extracted",
                {
                    "model": checked.model,
                    "consistency": checked.consistency.verdict,
                    "inserted": inserted,
                },
            )
        return inserted

    def get_paper(self, paper_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return dict(row) if row else None

    def list_claims(self, paper_id: str) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM claims WHERE paper_id = ? ORDER BY created_at, id",
                (paper_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_extraction(self, paper_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM paper_extractions
                WHERE paper_id = ? ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (paper_id,),
            ).fetchone()
        return dict(row) if row else None

    def record_event(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        detail: dict[str, object],
        *,
        actor: str = "system",
    ) -> None:
        with self.database.transaction() as connection:
            self._audit(connection, entity_type, entity_id, action, detail, actor=actor)

    @staticmethod
    def _audit(
        connection,
        entity_type: str,
        entity_id: str,
        action: str,
        detail: object,
        *,
        actor: str = "system",
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(entity_type, entity_id, action, actor, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_type, entity_id, action, actor, json.dumps(detail, ensure_ascii=False), _now()),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def _publication_status(record: PaperRecord) -> str:
    types = " ".join(record.publication_types).casefold()
    if "preprint" in types or record.source_id.startswith("PPR:"):
        return "preprint"
    if record.source.value in {"doaj", "europe_pmc"}:
        return "formal"
    return "unknown"


def _topic_dict(row) -> dict[str, object]:
    topic = dict(row)
    for field in (
        "picots_json",
        "eligible_study_designs_json",
        "inclusion_criteria_json",
        "exclusion_reasons_json",
        "required_search_streams_json",
    ):
        topic[field.removesuffix("_json")] = json.loads(topic.pop(field))
    return topic
