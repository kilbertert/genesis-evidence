"""Small SQLite boundary shared by domain-specific stores."""

from __future__ import annotations

import json
import sqlite3
import uuid
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
        "evidence_profiles": (
            "topic_id TEXT REFERENCES evidence_topics(id)",
            "scope_key TEXT NOT NULL DEFAULT ''",
        ),
        "knowledge_cards": ("evidence_profile_id TEXT REFERENCES evidence_profiles(id)",),
        "product_recommendations": ("recommendation_json TEXT NOT NULL DEFAULT '{}'",),
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
    _migrate_legacy_shared_results(connection)


def _migrate_legacy_shared_results(connection: sqlite3.Connection) -> None:
    """Split Results written before Result identity included the Claim identity."""

    groups = connection.execute(
        """
        SELECT result_id
        FROM claims
        WHERE result_id IS NOT NULL
        GROUP BY result_id
        HAVING count(*) > 1
        ORDER BY result_id
        """
    ).fetchall()
    for group in groups:
        old_result_id = str(group["result_id"])
        claims = connection.execute(
            """
            SELECT
                c.*,
                pe.extraction_json,
                r.study_id AS legacy_study_id,
                r.paper_id AS legacy_paper_id,
                r.extraction_id AS legacy_extraction_id,
                r.status AS legacy_status,
                r.created_at AS legacy_created_at
            FROM claims c
            JOIN paper_extractions pe ON pe.id = c.extraction_id
            JOIN results r ON r.id = c.result_id
            WHERE c.result_id = ?
            ORDER BY c.id
            """,
            (old_result_id,),
        ).fetchall()
        try:
            result_rows = [
                _legacy_result_row(claim, old_result_id) for claim in claims
            ]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            _record_migration_event_once(
                connection,
                entity_id=old_result_id,
                action="legacy_result_identity_split_skipped",
                detail={"reason": str(exc)},
            )
            continue

        new_ids = [row["id"] for row in result_rows]
        if len(set(new_ids)) != len(new_ids):
            _record_migration_event_once(
                connection,
                entity_id=old_result_id,
                action="legacy_result_identity_split_skipped",
                detail={"reason": "claim identities are not unique"},
            )
            continue
        if connection.execute(
            f"SELECT 1 FROM results WHERE id IN ({','.join('?' for _ in new_ids)}) LIMIT 1",
            new_ids,
        ).fetchone():
            _record_migration_event_once(
                connection,
                entity_id=old_result_id,
                action="legacy_result_identity_split_skipped",
                detail={"reason": "target Result identity already exists"},
            )
            continue

        profile_refs = connection.execute(
            """
            SELECT profile_id, interpretation
            FROM evidence_profile_results
            WHERE result_id = ?
            """,
            (old_result_id,),
        ).fetchall()
        profile_targets: dict[tuple[str, str], str] = {}
        for profile_ref in profile_refs:
            profile_id = str(profile_ref["profile_id"])
            selected_claim_ids = {
                row["claim_id"]
                for row in connection.execute(
                    """
                    SELECT cc.claim_id
                    FROM card_claims cc
                    JOIN knowledge_cards kc ON kc.id = cc.card_id
                    WHERE kc.evidence_profile_id = ?
                      AND cc.claim_id IN ({})
                    """.format(",".join("?" for _ in claims)),
                    [profile_id, *(claim["id"] for claim in claims)],
                ).fetchall()
            }
            for row in result_rows:
                if not selected_claim_ids or row["claim_id"] in selected_claim_ids:
                    profile_targets[(profile_id, row["id"])] = str(
                        profile_ref["interpretation"]
                    )

        stale_cards = connection.execute(
            """
            UPDATE knowledge_cards SET status = 'stale'
            WHERE status IN ('draft', 'in_review', 'approved', 'published')
              AND id IN (
                  SELECT card_id FROM card_claims
                  WHERE claim_id IN ({})
              )
            """.format(",".join("?" for _ in claims)),
            [claim["id"] for claim in claims],
        ).rowcount

        for row in result_rows:
            connection.execute(
                """
                INSERT INTO results(
                    id, study_id, paper_id, extraction_id, population,
                    baseline_nutrient_status, ingredient_name, ingredient_form,
                    dose, comparator, outcome, timepoint, effect_estimate,
                    statistical_details, evidence_text, locator, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["study_id"],
                    row["paper_id"],
                    row["extraction_id"],
                    row["population"],
                    row["baseline_nutrient_status"],
                    row["ingredient_name"],
                    row["ingredient_form"],
                    row["dose"],
                    row["comparator"],
                    row["outcome"],
                    row["timepoint"],
                    row["effect_estimate"],
                    row["statistical_details"],
                    row["evidence_text"],
                    row["locator"],
                    row["status"],
                    row["created_at"],
                ),
            )
        connection.execute(
            "DELETE FROM evidence_profile_results WHERE result_id = ?", (old_result_id,)
        )
        connection.executemany(
            """
            INSERT INTO evidence_profile_results(profile_id, result_id, interpretation)
            VALUES (?, ?, ?)
            """,
            [
                (profile_id, result_id, interpretation)
                for (profile_id, result_id), interpretation in profile_targets.items()
            ],
        )
        for row in result_rows:
            connection.execute(
                "UPDATE claims SET result_id = ? WHERE id = ?",
                (row["id"], row["claim_id"]),
            )
        connection.execute("DELETE FROM results WHERE id = ?", (old_result_id,))
        _record_migration_event_once(
            connection,
            entity_id=old_result_id,
            action="legacy_result_identity_split",
            detail={
                "claim_ids": [row["claim_id"] for row in result_rows],
                "new_result_ids": new_ids,
                "profile_result_count": len(profile_targets),
                "stale_cards": stale_cards,
            },
        )


def _legacy_result_row(claim: sqlite3.Row, old_result_id: str) -> dict[str, str]:
    payload = json.loads(str(claim["extraction_json"]))
    candidates = payload.get("claims")
    if not isinstance(candidates, list):
        raise ValueError(f"Result {old_result_id} extraction has no claims")
    matches = [
        item
        for item in candidates
        if isinstance(item, dict)
        and item.get("text") == claim["candidate_text"]
        and item.get("evidence") == claim["evidence_text"]
        and item.get("locator") == claim["locator"]
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Result {old_result_id} claim {claim['id']} has {len(matches)} source matches"
        )
    source = matches[0]
    fields = (
        "population",
        "baseline_nutrient_status",
        "ingredient_name",
        "ingredient_form",
        "dose",
        "comparator",
        "outcome",
        "timepoint",
        "effect_estimate",
        "statistical_details",
    )
    values = {field: source.get(field) for field in fields}
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError(f"Result {old_result_id} claim {claim['id']} has incomplete source fields")
    claim_id = str(claim["id"])
    result_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{claim['extraction_id']}\nresult\n{claim_id}"))
    return {
        "id": result_id,
        "claim_id": claim_id,
        "study_id": _required_legacy_text(claim, "legacy_study_id", old_result_id),
        "paper_id": _required_legacy_text(claim, "legacy_paper_id", old_result_id),
        "extraction_id": _required_legacy_text(claim, "legacy_extraction_id", old_result_id),
        **{field: str(values[field]) for field in fields},
        "evidence_text": str(claim["evidence_text"]),
        "locator": str(claim["locator"]),
        "status": _required_legacy_text(claim, "legacy_status", old_result_id),
        "created_at": _required_legacy_text(claim, "legacy_created_at", old_result_id),
    }


def _required_legacy_text(
    claim: sqlite3.Row, field: str, old_result_id: str
) -> str:
    value = claim[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Result {old_result_id} has no {field}")
    return value


def _record_migration_event_once(
    connection: sqlite3.Connection,
    *,
    entity_id: str,
    action: str,
    detail: dict[str, object],
) -> None:
    if connection.execute(
        "SELECT 1 FROM audit_events WHERE entity_type = 'result' AND entity_id = ? AND action = ?",
        (entity_id, action),
    ).fetchone():
        return
    connection.execute(
        """
        INSERT INTO audit_events(entity_type, entity_id, action, actor, detail_json, created_at)
        VALUES ('result', ?, ?, 'system:migration', ?, datetime('now'))
        """,
        (entity_id, action, json.dumps(detail, ensure_ascii=False, sort_keys=True)),
    )
