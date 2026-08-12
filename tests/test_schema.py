import json
import sqlite3

import pytest

from genesis_evidence.core.store import Database


def test_schema_has_only_two_line_tables_and_stays_under_budget(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()

    tables = set(database.table_names())
    assert len(tables) == 24
    assert {
        "conditions",
        "papers",
        "claims",
        "studies",
        "study_publications",
        "results",
        "paper_extractions",
        "claim_reviews",
        "evidence_profiles",
        "evidence_profile_results",
        "knowledge_cards",
        "card_claims",
        "reports",
        "report_files",
        "report_observations",
        "observation_confirmations",
        "assessments",
        "assessment_findings",
    } <= tables


def test_initialization_seeds_only_the_first_twelve_conditions(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    database.initialize()

    conditions = database.list_conditions()
    assert len(conditions) == 12
    assert conditions[0].code == "COND_ANEMIA_PATTERN"


@pytest.mark.parametrize("identifier", ["doi", "pmid", "pmcid"])
def test_papers_are_deduplicated_by_stable_identifier(tmp_path, identifier) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            f"INSERT INTO papers(id, title, {identifier}, created_at) VALUES (?, ?, ?, ?)",
            ("paper-1", "First", "same-id", "2026-08-11T00:00:00Z"),
        )
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute(
            f"INSERT INTO papers(id, title, {identifier}, created_at) VALUES (?, ?, ?, ?)",
            ("paper-2", "Duplicate", "same-id", "2026-08-11T00:00:00Z"),
        )


def test_published_card_requires_review_and_patient_content_in_database(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_cards(id, condition_code, version, status, created_at)
            VALUES (?, ?, ?, 'published', ?)
            """,
            ("card-1", "COND_DYSLIPIDEMIA", "1.0.0", "2026-08-11T00:00:00Z"),
        )


@pytest.mark.parametrize(
    ("source", "source_id", "expected"),
    [("europe_pmc", "MED:1", "formal"), ("europe_pmc", "PPR:1", "preprint")],
)
def test_existing_publication_status_is_backfilled_from_verified_source(
    tmp_path, source, source_id, expected
) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO papers(id, title, publication_status, created_at)
            VALUES ('paper-1', 'Existing paper', 'unknown', '2026-08-11T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO paper_sources(paper_id, source, source_id, source_url)
            VALUES ('paper-1', ?, ?, 'https://example.test/paper-1')
            """,
            (source, source_id),
        )
    database.initialize()
    with database.connect() as connection:
        status = connection.execute(
            "SELECT publication_status FROM papers WHERE id = 'paper-1'"
        ).fetchone()[0]
    assert status == expected


def test_legacy_claim_grade_is_archived_and_published_card_is_staled(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE conditions (
                code TEXT PRIMARY KEY, name TEXT NOT NULL, metrics_json TEXT NOT NULL,
                department TEXT NOT NULL, recheck_direction TEXT NOT NULL
            );
            CREATE TABLE papers (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, abstract TEXT NOT NULL DEFAULT '',
                doi TEXT, pmid TEXT, pmcid TEXT, year INTEGER, study_design_candidate TEXT,
                integrity_status TEXT NOT NULL DEFAULT 'unknown', created_at TEXT NOT NULL
            );
            CREATE TABLE paper_extractions (
                id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, model TEXT NOT NULL,
                extraction_run_id TEXT NOT NULL, extraction_json TEXT NOT NULL,
                check_model TEXT NOT NULL, check_run_id TEXT NOT NULL,
                consistency_status TEXT NOT NULL, consistency_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE claims (
                id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, extraction_id TEXT NOT NULL,
                candidate_text TEXT NOT NULL, evidence_text TEXT NOT NULL, locator TEXT NOT NULL,
                candidate_study_design TEXT, status TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT NOT NULL
            );
            CREATE TABLE claim_reviews (
                claim_id TEXT PRIMARY KEY, decision TEXT NOT NULL, corrected_text TEXT,
                corrected_study_design TEXT, inference TEXT, grade TEXT, condition_code TEXT,
                reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL
            );
            CREATE TABLE knowledge_cards (
                id TEXT PRIMARY KEY, condition_code TEXT NOT NULL, version TEXT NOT NULL,
                status TEXT NOT NULL, grade TEXT, reviewer TEXT, reviewed_at TEXT,
                published_at TEXT, patient_visible_body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
                detail_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            INSERT INTO conditions VALUES ('COND_DYSLIPIDEMIA', '血脂异常', '[]', '内科', '复查');
            INSERT INTO papers(id, title, integrity_status, created_at)
                VALUES ('paper-1', 'Legacy paper', 'clear', '2026-08-11T00:00:00Z');
            INSERT INTO paper_extractions VALUES (
                'extraction-1', 'paper-1', 'model', 'run-1', '{}', 'model', 'run-2',
                'consistent', '{}', '2026-08-11T00:00:00Z'
            );
            INSERT INTO claims VALUES (
                'claim-1', 'paper-1', 'extraction-1', 'Legacy claim', 'Evidence', 'Results',
                'cohort_study', 'reviewed', '2026-08-11T00:00:00Z'
            );
            INSERT INTO claim_reviews VALUES (
                'claim-1', 'approved', 'Corrected claim', 'cohort_study', 'associational',
                'moderate', 'COND_DYSLIPIDEMIA', 'reviewer-1', '2026-08-11T01:00:00Z'
            );
            INSERT INTO knowledge_cards VALUES (
                'card-1', 'COND_DYSLIPIDEMIA', '1.0.0', 'published', 'moderate',
                'reviewer-1', '2026-08-11T02:00:00Z', '2026-08-11T03:00:00Z',
                'Legacy patient content', '2026-08-11T02:00:00Z'
            );
            """
        )

    database = Database(path)
    database.initialize()
    database.initialize()

    with database.connect() as connection:
        claim = connection.execute("SELECT status FROM claims WHERE id = 'claim-1'").fetchone()
        review = connection.execute(
            "SELECT decision FROM claim_reviews WHERE claim_id = 'claim-1'"
        ).fetchone()
        card = connection.execute(
            "SELECT status FROM knowledge_cards WHERE id = 'card-1'"
        ).fetchone()
        events = connection.execute(
            """
            SELECT detail_json FROM audit_events
            WHERE action = 'legacy_claim_review_invalidated' AND entity_id = 'claim-1'
            """
        ).fetchall()
    assert claim["status"] == "candidate"
    assert review["decision"] == "rejected"
    assert card["status"] == "stale"
    assert len(events) == 1
    assert json.loads(events[0]["detail_json"])["grade"] == "moderate"
