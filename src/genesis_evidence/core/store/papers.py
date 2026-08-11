"""Persistence for literature collection, full text, and candidate claims."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ...literature.evidence import EvidenceExtraction
from ...literature.models import PaperRecord
from .database import Database


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

    def start_collection(self, *, condition_code: str, source: str, query: str) -> str:
        run_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO collection_runs(id, condition_code, source, query, status, created_at)
                VALUES (?, ?, ?, ?, 'running', ?)
                """,
                (run_id, condition_code, source, query, _now()),
            )
        return run_id

    def finish_collection(self, run_id: str, *, status: str, detail: dict[str, object]) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Collection status must be completed or failed")
        with self.database.transaction() as connection:
            updated = connection.execute(
                "UPDATE collection_runs SET status = ? WHERE id = ? AND status = 'running'",
                (status, run_id),
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
                INSERT INTO papers(id, title, abstract, doi, pmid, pmcid, year, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = CASE WHEN length(excluded.title) > length(papers.title)
                        THEN excluded.title ELSE papers.title END,
                    abstract = CASE WHEN length(excluded.abstract) > length(papers.abstract)
                        THEN excluded.abstract ELSE papers.abstract END,
                    doi = COALESCE(papers.doi, excluded.doi),
                    pmid = COALESCE(papers.pmid, excluded.pmid),
                    pmcid = COALESCE(papers.pmcid, excluded.pmcid),
                    year = COALESCE(papers.year, excluded.year)
                """,
                (
                    paper_id,
                    record.title,
                    record.abstract or "",
                    record.doi,
                    record.pmid,
                    record.pmcid,
                    record.publication_year,
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
            connection.execute(
                """
                INSERT INTO collection_papers(run_id, paper_id, position) VALUES (?, ?, ?)
                ON CONFLICT(run_id, paper_id) DO UPDATE SET
                    position = MIN(position, excluded.position)
                """,
                (run_id, paper_id, position),
            )

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
            connection.execute(
                "UPDATE papers SET integrity_status = ? WHERE id = ?",
                (status, paper_id),
            )
            self._audit(connection, "paper", paper_id, f"integrity_{status}", detail)

    def save_full_text(
        self,
        paper_id: str,
        stored: StoredObject,
        *,
        media_type: str,
        rights_status: str,
    ) -> None:
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

    def save_candidate_claims(self, paper_id: str, extraction: EvidenceExtraction) -> int:
        now = _now()
        with self.database.transaction() as connection:
            paper = connection.execute(
                "SELECT integrity_status FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if paper is None:
                raise ValueError("Paper does not exist")
            if paper["integrity_status"] == "retracted":
                raise ValueError("Candidate claims cannot be stored for a retracted paper")
            connection.execute(
                "UPDATE papers SET study_design_candidate = ? WHERE id = ?",
                (extraction.pico.study_design, paper_id),
            )
            inserted = 0
            for claim in extraction.claims:
                claim_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{paper_id}\n{claim.source_section}\n{claim.source_excerpt}\n{claim.claim_text}",
                    )
                )
                inserted += connection.execute(
                    """
                    INSERT OR IGNORE INTO claims(
                        id, paper_id, candidate_text, evidence_text, locator,
                        candidate_study_design, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim_id,
                        paper_id,
                        claim.claim_text,
                        claim.source_excerpt,
                        claim.source_section,
                        extraction.pico.study_design,
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
                {"extractor": extraction.extractor_name, "inserted": inserted},
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

    def record_event(
        self, entity_type: str, entity_id: str, action: str, detail: dict[str, object]
    ) -> None:
        with self.database.transaction() as connection:
            self._audit(connection, entity_type, entity_id, action, detail)

    @staticmethod
    def _audit(connection, entity_type: str, entity_id: str, action: str, detail: object) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(entity_type, entity_id, action, actor, detail_json, created_at)
            VALUES (?, ?, ?, 'system', ?, ?)
            """,
            (entity_type, entity_id, action, json.dumps(detail, ensure_ascii=False), _now()),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()
