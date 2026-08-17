"""Small SQLite boundary shared by domain-specific stores."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..conditions import CONDITIONS, ConditionDefinition
from .schema import SCHEMA


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            _migrate_existing_schema(connection)
            connection.executemany(
                """
                INSERT INTO conditions(code, name, metrics_json, department, recheck_direction)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    metrics_json = excluded.metrics_json,
                    department = excluded.department,
                    recheck_direction = excluded.recheck_direction
                """,
                [
                    (
                        item.code,
                        item.name,
                        json.dumps(item.metrics, ensure_ascii=False),
                        item.department,
                        item.recheck_direction,
                    )
                    for item in CONDITIONS
                ],
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def table_names(self) -> tuple[str, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        return tuple(row["name"] for row in rows)

    def list_conditions(self) -> tuple[ConditionDefinition, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT code, name, metrics_json, department, recheck_direction
                FROM conditions ORDER BY code
                """
            ).fetchall()
        return tuple(
            ConditionDefinition(
                code=row["code"],
                name=row["name"],
                metrics=tuple(json.loads(row["metrics_json"])),
                department=row["department"],
                recheck_direction=row["recheck_direction"],
            )
            for row in rows
        )


def _migrate_existing_schema(connection: sqlite3.Connection) -> None:
    additions = {
        "papers": ("publication_status TEXT NOT NULL DEFAULT 'unknown'",),
        "collection_runs": (
            "topic_id TEXT REFERENCES evidence_topics(id)",
            "search_stream TEXT NOT NULL DEFAULT 'effect'",
            "query_version TEXT NOT NULL DEFAULT '1'",
            "completed_at TEXT",
        ),
        "collection_papers": (
            "title_abstract_decision TEXT",
            "title_abstract_reviewer TEXT",
            "title_abstract_reviewed_at TEXT",
            "full_text_retrieval_status TEXT NOT NULL DEFAULT 'pending'",
            "full_text_retrieval_reason TEXT",
            "full_text_retrieval_reviewer TEXT",
            "full_text_retrieval_recorded_at TEXT",
            "full_text_decision TEXT",
            "primary_exclusion_reason TEXT",
            "full_text_reviewer TEXT",
            "full_text_reviewed_at TEXT",
        ),
        "paper_admissions": ("consistency_resolution TEXT",),
        "paper_extractions": (
            "second_model TEXT NOT NULL DEFAULT ''",
            "second_run_id TEXT NOT NULL DEFAULT ''",
            "second_extraction_json TEXT NOT NULL DEFAULT '{}'",
        ),
        "claims": (
            "result_id TEXT REFERENCES results(id)",
            "candidate_claim_type TEXT NOT NULL DEFAULT 'other'",
        ),
        "claim_reviews": (
            "risk_of_bias_json TEXT",
            "applicability TEXT",
        ),
        "evidence_profiles": ("topic_id TEXT REFERENCES evidence_topics(id)",),
        "knowledge_cards": ("evidence_profile_id TEXT REFERENCES evidence_profiles(id)",),
    }
    for table, columns in additions.items():
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for definition in columns:
            name = definition.split(maxsplit=1)[0]
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
    connection.execute(
        """
        UPDATE collection_papers SET
            full_text_retrieval_status = 'retrieved',
            full_text_retrieval_reason = NULL,
            full_text_retrieval_reviewer = COALESCE(
                full_text_retrieval_reviewer, 'system:migration'
            ),
            full_text_retrieval_recorded_at = COALESCE(
                full_text_retrieval_recorded_at, datetime('now')
            )
        WHERE EXISTS (
            SELECT 1 FROM full_texts ft WHERE ft.paper_id = collection_papers.paper_id
        )
        """
    )
    connection.execute(
        """
        UPDATE papers SET publication_status = 'preprint'
        WHERE publication_status = 'unknown' AND EXISTS (
            SELECT 1 FROM paper_sources ps
            WHERE ps.paper_id = papers.id AND ps.source_id LIKE 'PPR:%'
        )
        """
    )
    connection.execute(
        """
        UPDATE papers SET publication_status = 'formal'
        WHERE publication_status = 'unknown' AND EXISTS (
            SELECT 1 FROM paper_sources ps
            WHERE ps.paper_id = papers.id AND ps.source IN ('europe_pmc', 'doaj')
        )
        """
    )
    claim_review_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(claim_reviews)").fetchall()
    }
    if "grade" in claim_review_columns:
        legacy_reviews = [
            dict(row) for row in connection.execute("SELECT * FROM claim_reviews").fetchall()
        ]
        connection.executescript(
            """
            ALTER TABLE claim_reviews RENAME TO claim_reviews_legacy;
            CREATE TABLE claim_reviews (
                claim_id TEXT PRIMARY KEY REFERENCES claims(id) ON DELETE CASCADE,
                decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
                corrected_text TEXT,
                corrected_study_design TEXT CHECK (corrected_study_design IN (
                    'randomized_controlled_trial', 'systematic_review_meta_analysis',
                    'cohort_study', 'case_control_study', 'cross_sectional_study',
                    'controlled_feeding_metabolic_study',
                    'bioavailability_pharmacokinetic_study',
                    'biomarker_validation_study', 'non_randomized_controlled_study',
                    'natural_experiment', 'ecological_study', 'animal_study',
                    'in_vitro_study', 'case_series', 'case_report', 'guideline',
                    'other', 'uncertain'
                )),
                inference TEXT CHECK (inference IN ('causal', 'associational', 'descriptive')),
                risk_of_bias_json TEXT,
                applicability TEXT,
                condition_code TEXT REFERENCES conditions(code),
                reviewer TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                CHECK (decision = 'rejected' OR (
                    corrected_text IS NOT NULL AND corrected_study_design IS NOT NULL
                    AND trim(corrected_text) <> '' AND trim(corrected_study_design) <> ''
                    AND inference IS NOT NULL AND risk_of_bias_json IS NOT NULL
                    AND applicability IS NOT NULL AND trim(applicability) <> ''
                    AND condition_code IS NOT NULL
                ))
            );
            INSERT INTO claim_reviews(claim_id, decision, reviewer, reviewed_at)
            SELECT claim_id, 'rejected', reviewer, reviewed_at FROM claim_reviews_legacy;
            UPDATE claims SET status = 'candidate'
            WHERE id IN (SELECT claim_id FROM claim_reviews_legacy WHERE decision = 'approved');
            DROP TABLE claim_reviews_legacy;
            """
        )
        connection.executemany(
            """
            INSERT INTO audit_events(
                entity_type, entity_id, action, actor, detail_json, created_at
            ) VALUES ('claim', ?, 'legacy_claim_review_invalidated', ?, ?, ?)
            """,
            [
                (
                    row["claim_id"],
                    row["reviewer"],
                    json.dumps(row, ensure_ascii=False),
                    row["reviewed_at"],
                )
                for row in legacy_reviews
            ],
        )
    connection.execute(
        """
        UPDATE knowledge_cards SET status = 'stale'
        WHERE status = 'published' AND (
            evidence_profile_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM evidence_profiles ep
                WHERE ep.id = knowledge_cards.evidence_profile_id AND ep.topic_id IS NOT NULL
            )
        )
        """
    )
