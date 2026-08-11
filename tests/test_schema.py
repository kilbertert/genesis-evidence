import sqlite3

import pytest

from genesis_evidence.core.store import Database


def test_schema_has_only_two_line_tables_and_stays_under_budget(tmp_path) -> None:
    database = Database(tmp_path / "evidence.sqlite3")
    database.initialize()

    tables = set(database.table_names())
    assert len(tables) == 19
    assert {
        "conditions",
        "papers",
        "claims",
        "paper_extractions",
        "claim_reviews",
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
